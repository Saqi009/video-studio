"""`vs build`: source footage -> edited, branded, scored, captioned MP4."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import ff, graphics
from .config import AUDIO_EXT, VIDEO_EXT, StudioError, load_settings
from .ff import log


def _files(folder: Path, exts: set[str]) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in exts) if folder.exists() else []


def source_files(project: Path, cfg: dict) -> list[Path]:
    listed = [project / f for f in cfg["source"]["files"]]
    files = listed or _files(project / "source", VIDEO_EXT) or \
        [p for d in sorted((project / "source").glob("*/")) for p in _files(d, VIDEO_EXT)]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise StudioError(f"Source files not found: {', '.join(missing)}")
    if not files:
        raise StudioError(f"No footage. Put video files in {project / 'source'} or run: ./vs tella pull ...")
    return files


def music_file(project: Path, cfg: dict) -> Path | None:
    if cfg["music"]["file"]:
        f = project / cfg["music"]["file"]
        if not f.exists():
            raise StudioError(f"Music file not found: {f}")
        return f
    found = _files(project / "music", AUDIO_EXT)
    return found[0] if found else None


def _filter_path(p: Path) -> str:
    """Escape a path for use inside an FFmpeg filtergraph value."""
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def transcribe(video: Path, out_srt: Path, cfg: dict) -> Path | None:
    cmd = [*graphics._hyperframes(), "transcribe", str(video), "--to", "srt", "-o", str(out_srt),
           "--model", cfg["captions"]["model"], "--language", cfg["captions"]["language"], "--optional"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_srt.exists():
        log("captions skipped: transcription was not available (" + (proc.stderr or proc.stdout).strip()[-300:] + ")")
        return None
    return out_srt


def build(project: Path, draft: bool = False, style: str | None = None) -> Path:
    cfg = load_settings(project)
    style = style or cfg["edit"]["style"]
    if style == "beat":
        from .beat import build_beat
        (project / "build").mkdir(exist_ok=True)
        (project / "output").mkdir(exist_ok=True)
        return build_beat(project, cfg, source_files(project, cfg), music_file(project, cfg), draft)
    if style != "talk":
        raise StudioError(f"edit.style must be talk or beat (got {style!r})")
    out_cfg, audio, edit, gfx, brand = cfg["output"], cfg["audio"], cfg["edit"], cfg["graphics"], cfg["brand"]
    W, H, FPS = int(out_cfg["width"]), int(out_cfg["height"]), int(out_cfg["fps"])
    build_dir, out_dir = project / "build", project / "output"
    build_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    slug = project.name

    # 1. Conform: every source clip to one size, frame rate and audio format, joined in order.
    sources = source_files(project, cfg)
    log(f"conforming {len(sources)} source file(s) to {W}x{H}@{FPS}")
    conformed = ff.conform(sources, build_dir / "conformed.mov", W, H, FPS, bool(audio["voice_cleanup"]))
    total_src = ff.duration(conformed)

    # 2. Tighten: drop dead air longer than min_silence, keeping a little room around speech.
    segments = [(0.0, total_src)]
    main = conformed
    if edit["trim_silence"]:
        silences = ff.detect_silence(conformed, float(edit["silence_threshold_db"]), float(edit["min_silence"]))
        segments = ff.keep_segments(total_src, silences, float(edit["keep_pad"]))
        if len(segments) > 1:
            main = ff.cut(conformed, segments, build_dir / "edited.mov", build_dir)
    tmap = ff.TimeMap(segments)
    main_d = ff.duration(main)
    log(f"edit: {total_src:.1f}s -> {main_d:.1f}s ({len(segments)} segment(s), {total_src - main_d:.1f}s of silence removed)")

    # 3. Captions (times relative to the edited main section).
    cues = []
    cap = cfg["captions"]
    if cap["mode"] == "transcribe":
        log("transcribing for captions (local Whisper via HyperFrames)")
        srt = transcribe(main, build_dir / "captions.srt", cfg)
        cues = ff.read_srt(srt) if srt else []
    elif cap["mode"] == "file" and cap["file"]:
        cues = ff.remap_srt(ff.read_srt(project / cap["file"]), tmap)  # file is timed to the raw recording
    elif cap["mode"] not in ("none", "file"):
        raise StudioError(f"captions.mode must be none, transcribe or file (got {cap['mode']!r})")

    # 4. Graphics from the HyperFrames templates.
    gdir = build_dir / "graphics"
    bv = graphics.brand_vars(brand)
    intro = outro = None
    if gfx["intro"]:
        log("rendering intro (HyperFrames)")
        intro = graphics.render("intro", {**bv, "title": cfg.get("title", slug), "subtitle": cfg.get("subtitle", "")}, gdir, FPS)
    if gfx["outro"]:
        log("rendering outro (HyperFrames)")
        outro = graphics.render("outro", {**bv, "cta": gfx["outro_cta"], "subtext": gfx["outro_subtext"]}, gdir, FPS)
    lower = []
    for lt in gfx["lower_thirds"]:
        at = tmap(float(lt.get("at", 0)))
        if at is None or at >= main_d:
            log(f"lower third '{lt.get('name')}' skipped: {lt.get('at')}s is past the end of the edit")
            continue
        d = float(lt.get("duration", 5))
        log(f"rendering lower third '{lt.get('name', '')}' at {at:.1f}s (HyperFrames)")
        mov = graphics.render("lower-third", {"name": lt.get("name", ""), "role": lt.get("role", ""), "duration": d,
                                              "ink": brand["text"], "panel": brand["background"],
                                              "accent": brand["accent"], "font": brand["font"]},
                              gdir, FPS, fmt="mov", duration_override=d)
        lower.append((mov, at, d))

    # 5. Assemble everything in one FFmpeg pass: overlays, captions, intro/outro, music, loudness.
    music = music_file(project, cfg)
    intro_d = ff.duration(intro) if intro else 0.0
    outro_d = ff.duration(outro) if outro else 0.0
    total = intro_d + main_d + outro_d

    inputs = ["-i", str(main)]
    idx = 1
    g: list[str] = []
    vchain = "[0:v]"
    for k, (mov, at, d) in enumerate(lower):
        inputs += ["-i", str(mov)]
        g.append(f"[{idx}:v]setpts=PTS-STARTPTS+{at:.3f}/TB[lt{k}]")
        g.append(f"{vchain}[lt{k}]overlay=eof_action=pass:enable='between(t,{at:.3f},{at + d:.3f})'[mv{k}]")
        vchain = f"[mv{k}]"
        idx += 1
    if cues and cap["burn"]:
        srt_main = ff.write_srt(cues, build_dir / "captions-main.srt")
        font = brand["font"].split(",")[0].strip().strip("'\"")
        style = (f"FontName={font},FontSize={int(cap['font_size'])},Bold=1,PrimaryColour={_ass_colour(brand['text'])},"
                 f"BorderStyle=3,OutlineColour={_ass_colour(brand['background'], 0x60)},Outline=6,Shadow=0,MarginV=18")
        g.append(f"{vchain}subtitles=filename='{_filter_path(srt_main)}':force_style='{style}'[mvs]")
        vchain = "[mvs]"
    g.append(f"{vchain}null[mainv]")
    g.append(f"[0:a]loudnorm=I=-18:TP=-2:LRA=11,aresample=48000[maina]")

    parts = []
    for name, clip, d in (("intro", intro, intro_d), ("main", None, main_d), ("outro", outro, outro_d)):
        if name == "main":
            parts.append("[mainv][maina]")
            continue
        if not clip:
            continue
        inputs += ["-i", str(clip)]
        g.append(f"[{idx}:v]scale={W}:{H},fps={FPS},format=yuv420p,setsar=1[{name}v]")
        g.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f}[{name}a]")
        parts.append(f"[{name}v][{name}a]")
        idx += 1
    g.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=1[vcat][voice]")

    if music:
        log(f"scoring with {music.name}" + (" (ducked under speech)" if audio["duck"] else ""))
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        fo = float(audio["music_fade_out"])
        g.append(f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"loudnorm=I={float(audio['music_lufs'])}:TP=-3:LRA=11,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                 f"afade=t=in:d={float(audio['music_fade_in'])},afade=t=out:st={max(0.0, total - fo):.3f}:d={fo}[mus]")
        if audio["duck"]:
            g.append("[voice]asplit=2[vox][key]")
            g.append("[mus][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=700:makeup=1[bed]")
            g.append("[vox][bed]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            g.append("[voice][mus]amix=inputs=2:duration=first:normalize=0[mix]")
    else:
        log("no music (add a file to music/ or run: ./vs music get ...)")
        g.append("[voice]anull[mix]")
    g.append(f"[mix]loudnorm=I={float(audio['target_lufs'])}:TP={float(audio['true_peak'])}:LRA=11,aresample=48000[aout]")

    if draft:
        g.append("[vcat]scale=-2:720[vout]")
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]
    else:
        g.append("[vcat]null[vout]")
        venc = ["-c:v", "libx264", "-preset", str(out_cfg["preset"]), "-crf", str(out_cfg["crf"]), "-profile:v", "high"]

    script = build_dir / "final.filter"
    script.write_text(";\n".join(g))
    final = out_dir / (f"{slug}-draft.mp4" if draft else f"{slug}.mp4")
    log(f"exporting {final.name} ({total:.1f}s)")
    ff.run([*inputs, "-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]", *venc,
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-r", str(FPS), "-c:a", "aac", "-b:a", str(out_cfg["audio_bitrate"]), "-ar", "48000",
            "-movflags", "+faststart", "-t", f"{total:.3f}", str(final)])

    lufs = ff.match_loudness(final, float(audio["target_lufs"]), float(audio["true_peak"]), str(out_cfg["audio_bitrate"]))
    log(f"loudness: {lufs:.1f} LUFS (target {float(audio['target_lufs']):.0f})")

    # 6. Extras for upload: caption sidecar (timed to the final video) and a thumbnail candidate.
    if cues:
        ff.write_srt(ff.shift_srt(cues, intro_d), out_dir / f"{slug}.srt")
    ff.run(["-ss", f"{min(1.5, total / 2):.2f}", "-i", str(final), "-frames:v", "1", "-q:v", "2",
            str(out_dir / f"{slug}-thumbnail.jpg")])
    (build_dir / "edit-report.json").write_text(json.dumps({
        "sources": [str(s.relative_to(project)) for s in sources],
        "source_seconds": round(total_src, 3), "edited_seconds": round(main_d, 3),
        "final_seconds": round(total, 3), "kept_segments": [[round(a, 3), round(b, 3)] for a, b in segments],
        "music": music.name if music else None, "captions": len(cues),
    }, indent=2))
    log(f"done -> {final}")
    return final
