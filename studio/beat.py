"""Beat-synced montage: raw clips cut to the music's beats, with transitions landing on the beat.

How the timeline is built
-------------------------
1. HyperFrames' beat detector gives the beat times of the music track.
2. Boundaries B_0 < B_1 < ... are picked on beats, every N beats (N cycles through `pattern`).
   With an intro card, the music is shifted so a beat lands exactly where the intro ends.
3. Every piece of footage is cut to span [B_k - d_in/2, B_k+1 + d_out/2], where d is the length of
   the transition at that boundary. Chaining pieces with FFmpeg `xfade` at offset (B_k - d/2) then
   centres every transition exactly on its beat (a "cut" is a one-frame xfade, i.e. a hard cut).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import ff, graphics
from .config import StudioError
from .ff import log

# xfade transition names; "cut" = hard cut on the beat
# Mostly hard cuts on the beat, with an effect every third cut, so the effects stay special.
DEFAULT_TRANSITIONS = ["cut", "cut", "fadewhite", "cut", "cut", "zoomin", "cut", "cut", "slideleft",
                       "cut", "cut", "circleopen", "cut", "cut", "smoothleft", "cut", "cut", "fade"]


def detect_beats(music: Path) -> tuple[list[float], float]:
    cmd = [*graphics._hyperframes(), "beats", str(music), "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {}
    beats = [float(t) for t in data.get("beatTimes", [])]
    if proc.returncode != 0 or len(beats) < 8:
        raise StudioError("Could not find a steady beat in the music track. Try a track with a clear rhythm.\n"
                          + (proc.stderr or proc.stdout)[-800:])
    return beats, float(data.get("bpm") or 60 / ((beats[-1] - beats[0]) / (len(beats) - 1)))


def _pieces_plan(beats: list[float], start_idx: int, end_time: float, pattern: list[int], unit: int) -> list[int]:
    """Beat indices of the cut points, from start_idx while they stay before end_time."""
    idx, out, i = start_idx, [start_idx], 0
    while True:
        nxt = idx + pattern[i % len(pattern)] * unit
        if nxt >= len(beats) or beats[nxt] > end_time:
            break
        out.append(nxt)
        idx, i = nxt, i + 1
    return out


def _windows(sources: list[tuple[Path, float]], lengths: list[float], order: str) -> list[tuple[Path, float]]:
    """Choose (clip, start time) for each piece, spreading picks across each clip from beginning to end."""
    n = len(lengths)
    total = sum(d for _, d in sources)
    shares = [max(1, round(n * d / total)) for _, d in sources]
    while sum(shares) > n:  # trim the biggest shares until the count fits
        shares[shares.index(max(shares))] -= 1
    while sum(shares) < n:
        shares[shares.index(max(shares))] += 1
    slots: list[list[int]] = []  # piece indices per clip
    if order == "mix":  # interleave: round-robin over clips that still have share left
        left, clip_ids = list(shares), []
        while sum(left):
            for c in range(len(sources)):
                if left[c]:
                    clip_ids.append(c)
                    left[c] -= 1
    else:
        clip_ids = [c for c in range(len(sources)) for _ in range(shares[c])]
    for c in range(len(sources)):
        slots.append([k for k, cid in enumerate(clip_ids) if cid == c])

    picks: list[tuple[Path, float]] = [None] * n  # type: ignore[list-item]
    for c, (path, dur) in enumerate(sources):
        ks = slots[c]
        for j, k in enumerate(ks):
            need = lengths[k]
            lo, hi = min(0.3, dur * 0.05), max(0.0, dur - need - 0.2)
            start = lo if len(ks) == 1 or hi <= lo else lo + (hi - lo) * j / (len(ks) - 1)
            picks[k] = (path, max(0.0, min(start, max(0.0, dur - need))))
    return picks


def _render_piece(src: Path, start: float, length: float, out: Path, W: int, H: int, fps: int,
                  zoom: float, zoom_in: bool, fit: str) -> Path:
    if fit == "auto":  # clips shot the other way round (phone video in a 16:9 film) get a blurred backdrop
        v = next(st for st in ff.probe(src)["streams"] if st.get("codec_type") == "video")
        w, h = int(v["width"]), int(v["height"])
        if abs(int(v.get("tags", {}).get("rotate", 0) or 0)) in (90, 270) or any(
                abs(int(sd.get("rotation", 0) or 0)) in (90, 270) for sd in v.get("side_data_list", [])):
            w, h = h, w
        fit = "blur" if (w >= h) != (W >= H) else "cover"
    if fit == "contain":
        frame = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"
    elif fit == "blur":
        frame = (f"split=2[bg][fg];[bg]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                 f"boxblur=10:2,scale={W}:{H},eq=brightness=-0.08[bgb];"
                 f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    else:  # cover: fill the frame, crop the overflow
        frame = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if zoom > 0:  # slow push in / pull out across the piece
        z = f"(1+{zoom}*t/{length:.3f})" if zoom_in else f"(1+{zoom}-{zoom}*t/{length:.3f})"
        frame += f",scale=w='2*trunc({W}*{z}/2)':h='2*trunc({H}*{z}/2)':eval=frame,crop={W}:{H}"
    vf = f"{frame},setsar=1,fps={fps},tpad=stop_mode=clone:stop_duration={length:.3f},trim=0:{length:.3f},setpts=PTS-STARTPTS,format=yuv420p"
    ff.run(["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length + 0.2:.3f}", "-filter_complex", f"[0:v]{vf}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", str(out)])
    return out


def build_beat(project: Path, cfg: dict, sources: list[Path], music: Path | None, draft: bool) -> Path:
    if not music:
        raise StudioError("Beat mode needs a music track: put one in music/ (or run ./vs music get ...).")
    bc, out_cfg, audio, gfx, brand = cfg["beat"], cfg["output"], cfg["audio"], cfg["graphics"], cfg["brand"]
    W, H, FPS = int(out_cfg["width"]), int(out_cfg["height"]), int(out_cfg["fps"])
    build_dir, out_dir = project / "build", project / "output"
    pieces_dir = build_dir / "beat-pieces"
    pieces_dir.mkdir(parents=True, exist_ok=True)
    for old in pieces_dir.glob("*.mp4"):
        old.unlink()
    slug = project.name

    beats, bpm = detect_beats(music)
    period = (beats[-1] - beats[0]) / (len(beats) - 1)
    unit = 2 if bpm > 150 else 1  # very fast songs: count in half-time so cuts aren't frantic
    log(f"music: {music.name}, {bpm:.0f} BPM, {len(beats)} beats")

    # Intro card ends exactly on a beat: start the music so beat k0 lands at intro_d.
    intro = graphics.render("intro", {**graphics.brand_vars(brand), "title": cfg.get("title", slug),
                                      "subtitle": cfg.get("subtitle", "")}, build_dir / "graphics", FPS) if gfx["intro"] else None
    outro = graphics.render("outro", {**graphics.brand_vars(brand), "cta": gfx["outro_cta"],
                                      "subtext": gfx["outro_subtext"]}, build_dir / "graphics", FPS) if gfx["outro"] else None
    intro_d = ff.duration(intro) if intro else 0.0
    outro_d = ff.duration(outro) if outro else 0.0
    k0 = next((i for i, b in enumerate(beats) if b >= max(intro_d, beats[0])), 0)
    music_offset = beats[k0] - intro_d  # music time that plays at video time 0
    if music_offset < 0:
        raise StudioError("The music is too short before its first beat for the intro; set graphics.intro = false.")
    tl = [b - music_offset for b in beats]  # beat times on the video timeline

    music_d = ff.duration(music)
    footage = [(p, ff.duration(p)) for p in sources]
    max_s = float(bc["max_seconds"]) or 1e9
    end_limit = min(music_d - music_offset - outro_d - 0.5, intro_d + max_s)
    cuts = _pieces_plan(tl, k0, end_limit, [int(x) for x in bc["pattern"]], unit)
    if len(cuts) < 2:
        raise StudioError("Not enough music after the intro for even one cut; use a longer track.")
    bounds = [tl[i] for i in cuts]  # B_0 (= intro end, or the first beat) ... B_last (outro start)
    n = len(bounds) - 1
    frame = 1 / FPS
    td = min(float(bc["transition_beats"]) * period * unit, 0.6)
    names = bc["transitions"] or DEFAULT_TRANSITIONS
    # transition at each boundary: intro->first piece, between pieces, last piece->outro
    trans = []
    for k in range(n + 1):
        name = "fade" if k in (0, n) and (intro if k == 0 else outro) else names[k % len(names)]
        if k == 0 and not intro:
            name = "cut"
        trans.append(("fade", frame) if name == "cut" else (name, td))
    lengths = [bounds[k + 1] - bounds[k] + (trans[k][1] + trans[k + 1][1]) / 2 for k in range(n)]
    if not intro:  # the first piece starts at time 0, not half a transition early
        lengths[0] = bounds[1] - bounds[0] + trans[1][1] / 2

    picks = _windows(footage, lengths, bc["order"])
    log(f"cutting {n} pieces from {len(sources)} clip(s), every {bc['pattern']} beat(s)")
    pieces = []
    for k, ((src, start), L) in enumerate(zip(picks, lengths)):
        # +3 frames of margin: whole-frame rounding must never leave a piece shorter than its xfade
        # offset, or xfade ends the stream early. xfade discards the extra frames.
        pieces.append(_render_piece(src, start, L + 3 / FPS, pieces_dir / f"p{k:03}.mp4", W, H, FPS,
                                    float(bc["zoom"]), k % 2 == 0, bc["fit"]))

    # Video: chain everything with xfade, each transition centred on its beat.
    inputs, g = [], []
    chain = []  # (input label, length, transition into it)
    idx = 0
    if intro:
        inputs += ["-i", str(intro)]
        L0 = bounds[0] + trans[0][1] / 2
        g.append(f"[{idx}:v]scale={W}:{H},fps={FPS},format=yuv420p,setsar=1,tpad=stop_mode=clone:stop_duration=1,"
                 f"trim=0:{L0:.3f},setpts=PTS-STARTPTS,settb=AVTB[c{idx}]")
        chain.append((f"[c{idx}]", L0))
        idx += 1
    for k, p in enumerate(pieces):
        inputs += ["-i", str(p)]
        g.append(f"[{idx}:v]settb=AVTB,setpts=PTS-STARTPTS[c{idx}]")
        chain.append((f"[c{idx}]", lengths[k]))
        idx += 1
    if outro:
        inputs += ["-i", str(outro)]
        Lo = outro_d + trans[n][1] / 2
        g.append(f"[{idx}:v]scale={W}:{H},fps={FPS},format=yuv420p,setsar=1,tpad=start_mode=clone:start_duration={trans[n][1] / 2:.3f},"
                 f"trim=0:{Lo:.3f},setpts=PTS-STARTPTS,settb=AVTB[c{idx}]")
        chain.append((f"[c{idx}]", Lo))
        idx += 1

    acc, acc_len = chain[0]
    first_boundary = 0 if intro else 1  # which boundary the second chain element starts at
    for j, (label, L) in enumerate(chain[1:], start=1):
        k = j - 1 + first_boundary  # boundary index this join sits on
        name, d = trans[k]
        offset = acc_len - d
        out = f"[x{j}]"
        g.append(f"{acc}{label}xfade=transition={name}:duration={d:.4f}:offset={offset:.4f}{out}")
        acc, acc_len = out, acc_len + L - d
    total = acc_len

    # Audio: the music, aligned so its beats sit under the cuts, optionally with the clips' own sound.
    inputs += ["-ss", f"{music_offset:.3f}", "-i", str(music)]
    m_idx = idx
    fo = min(float(audio["music_fade_out"]) + 1, outro_d or 3.0)
    g.append(f"[{m_idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:{total:.3f},"
             f"asetpts=PTS-STARTPTS,afade=t=out:st={max(0.0, total - fo):.3f}:d={fo:.3f}[mus]")
    clip_vol = float(bc["clip_audio"])
    if clip_vol > 0:
        parts, a_inputs = [], []
        for k, ((src, start), L) in enumerate(zip(picks, lengths)):
            seg = bounds[k + 1] - bounds[k]
            a_inputs += ["-ss", f"{start + (L - seg) / 2:.3f}", "-t", f"{seg:.3f}", "-i", str(src)]
        base = m_idx + 1
        for k in range(n):
            src = picks[k][0]
            seg = bounds[k + 1] - bounds[k]
            if ff.has_audio(src):
                g.append(f"[{base + k}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                         f"apad,atrim=0:{seg:.3f},afade=t=in:d=0.03,afade=t=out:st={max(0, seg - 0.05):.3f}:d=0.05[ca{k}]")
            else:
                g.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{seg:.3f}[ca{k}]")
            parts.append(f"[ca{k}]")
        lead = f"anullsrc=r=48000:cl=stereo,atrim=0:{bounds[0]:.3f}[lead];" if bounds[0] > 0 else ""
        g.append(f"{lead}{'[lead]' if lead else ''}{''.join(parts)}concat=n={n + (1 if lead else 0)}:v=0:a=1,"
                 f"volume={clip_vol},apad,atrim=0:{total:.3f}[clips]")
        g.append("[mus][clips]amix=inputs=2:duration=first:normalize=0[mix]")
        inputs += a_inputs
    else:
        g.append("[mus]anull[mix]")
    g.append(f"[mix]loudnorm=I={float(audio['target_lufs'])}:TP={float(audio['true_peak'])}:LRA=11,aresample=48000[aout]")
    g.append(f"{acc}scale=-2:720[vout]" if draft else f"{acc}null[vout]")

    venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"] if draft else \
        ["-c:v", "libx264", "-preset", str(out_cfg["preset"]), "-crf", str(out_cfg["crf"]), "-profile:v", "high"]
    script = build_dir / "beat.filter"
    script.write_text(";\n".join(g))
    final = out_dir / (f"{slug}-draft.mp4" if draft else f"{slug}.mp4")
    log(f"exporting {final.name} ({total:.1f}s, {n} cuts on the beat)")
    ff.run([*inputs, "-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]", *venc,
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-r", str(FPS), "-c:a", "aac", "-b:a", str(out_cfg["audio_bitrate"]), "-ar", "48000",
            "-movflags", "+faststart", "-t", f"{total:.3f}", str(final)])
    lufs = ff.match_loudness(final, float(audio["target_lufs"]), float(audio["true_peak"]), str(out_cfg["audio_bitrate"]))
    log(f"loudness: {lufs:.1f} LUFS")
    (build_dir / "edit-report.json").write_text(json.dumps({
        "style": "beat", "bpm": round(bpm, 1), "music": music.name, "music_offset": round(music_offset, 3),
        "cuts_at": [round(b, 3) for b in bounds], "transitions": [t[0] if t[1] > frame else "cut" for t in trans],
        "pieces": [{"clip": p.name, "from": round(s, 3), "seconds": round(L, 3)} for (p, s), L in zip(picks, lengths)],
        "final_seconds": round(total, 3)}, indent=2))
    ff.run(["-ss", f"{min(1.5, total / 2):.2f}", "-i", str(final), "-frames:v", "1", "-q:v", "2",
            str(out_dir / f"{slug}-thumbnail.jpg")])
    log(f"done -> {final}")
    return final
