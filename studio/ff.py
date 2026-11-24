"""FFmpeg/FFprobe operations: conform, silence trim, time remapping, SRT handling."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from .config import StudioError

Segment = tuple[float, float]


def run(args: list[str], quiet: bool = True) -> str:
    cmd = ["ffmpeg", "-hide_banner", "-y", *(["-loglevel", "error"] if quiet else []), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StudioError("FFmpeg failed:\n" + proc.stderr[-2500:])
    return proc.stderr


def probe(path: Path) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise StudioError(f"Cannot read media file {path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path)["streams"])


def intermediate_codec() -> list[str]:
    """Near-lossless, fast-to-encode intermediate so quality is only lost once, at export."""
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]


def conform(sources: list[Path], out: Path, w: int, h: int, fps: int, voice_cleanup: bool) -> Path:
    """Join all source clips into one file at the project's size/fps with clean 48 kHz stereo audio."""
    inputs, chains, pads = [], [], []
    for i, src in enumerate(sources):
        inputs += ["-i", str(src)]
        chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p[v{i}]")
        if has_audio(src):
            chains.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{duration(src):.3f}[a{i}]")
        pads.append(f"[v{i}][a{i}]")
    graph = ";".join(chains) + f";{''.join(pads)}concat=n={len(sources)}:v=1:a=1[v][araw]"
    voice = "highpass=f=80,afftdn=nf=-28,acompressor=threshold=-20dB:ratio=2.5:attack=15:release=200:makeup=2" \
        if voice_cleanup else "anull"
    graph += f";[araw]{voice}[a]"
    run([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", *intermediate_codec(), str(out)])
    return out


def detect_silence(path: Path, threshold_db: float, min_silence: float) -> list[Segment]:
    log = run(["-i", str(path), "-vn", "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}",
               "-f", "null", "-"], quiet=False)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    total = duration(path)
    if len(ends) < len(starts):  # silence runs to the end of the file
        ends.append(total)
    return [(max(0.0, s), e) for s, e in zip(starts, ends)]


def keep_segments(total: float, silences: list[Segment], pad: float) -> list[Segment]:
    """Invert the silences, keeping `pad` seconds of room on each side of speech."""
    keep, cursor = [], 0.0
    for s, e in silences:
        cut_from, cut_to = s + pad, e - pad
        if cut_to - cut_from < 0.25:  # too short to be worth a cut
            continue
        if cut_from > cursor:
            keep.append((cursor, cut_from))
        cursor = cut_to
    if total - cursor > 0.05:
        keep.append((cursor, total))
    # a sliver of room left before leading / after trailing silence is not worth keeping
    if len(keep) > 1 and keep[0][0] == 0.0 and keep[0][1] - keep[0][0] < 0.3:
        keep.pop(0)
    if len(keep) > 1 and keep[-1][1] == total and keep[-1][1] - keep[-1][0] < 0.3:
        keep.pop()
    return keep or [(0.0, total)]


def cut(path: Path, segments: list[Segment], out: Path, workdir: Path) -> Path:
    """Keep only `segments` of `path`, joined back to back with 10 ms audio fades so cuts don't click."""
    parts, pads = [], []
    for i, (a, b) in enumerate(segments):
        fo = max(0.0, (b - a) - 0.01)
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.01,afade=t=out:st={fo:.3f}:d=0.01[a{i}]")
        pads.append(f"[v{i}][a{i}]")
    parts.append(f"{''.join(pads)}concat=n={len(segments)}:v=1:a=1[v][a]")
    script = workdir / "cut.filter"
    script.write_text(";\n".join(parts))
    run(["-i", str(path), "-filter_complex_script", str(script), "-map", "[v]", "-map", "[a]",
         *intermediate_codec(), str(out)])
    return out


class TimeMap:
    """Maps a time in the conformed source to the same moment in the silence-trimmed edit."""

    def __init__(self, segments: list[Segment]):
        self.segments = segments

    def __call__(self, t: float) -> float | None:
        offset = 0.0
        for a, b in self.segments:
            if a <= t <= b:
                return offset + (t - a)
            if t < a:  # t fell in a removed gap: snap to the next kept moment
                return offset
            offset += b - a
        return None


# ---- SRT ---------------------------------------------------------------------------------------

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _parse_ts(s: str) -> float:
    h, m, sec, ms = _TS.match(s.strip()).groups()
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms.ljust(3, "0")[:3]) / 1000


def _fmt_ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def read_srt(path: Path) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.strip().splitlines()
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")
        cues.append((_parse_ts(a), _parse_ts(b.split()[0]), "\n".join(lines[idx + 1:])))
    return cues


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> Path:
    path.write_text("".join(f"{i}\n{_fmt_ts(a)} --> {_fmt_ts(b)}\n{text}\n\n"
                            for i, (a, b, text) in enumerate(cues, 1)), encoding="utf-8")
    return path


def remap_srt(cues, tmap: TimeMap, shift: float = 0.0):
    out = []
    for a, b, text in cues:
        na, nb = tmap(a), tmap(b)
        if na is None or nb is None or nb - na < 0.2:  # cue was (almost) entirely cut out
            continue
        out.append((na + shift, nb + shift, text))
    return out


def shift_srt(cues, shift: float):
    return [(a + shift, b + shift, t) for a, b, t in cues]


def integrated_lufs(path: Path) -> float:
    log_text = run(["-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"], quiet=False)
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", log_text)
    return float(found[-1]) if found else float("nan")


def match_loudness(path: Path, target: float, true_peak: float, bitrate: str) -> float:
    """Single-pass loudnorm lands within ~1 LU; measure the result and trim the gain (audio-only re-encode)."""
    measured = integrated_lufs(path)
    gain = target - measured
    if measured != measured or abs(gain) < 0.3:
        return measured
    tmp = path.with_name(path.stem + ".loud" + path.suffix)
    limit = 10 ** (true_peak / 20)
    run(["-i", str(path), "-map", "0", "-c:v", "copy", "-af", f"volume={gain:.2f}dB,alimiter=limit={limit:.3f}:level=false",
         "-c:a", "aac", "-b:a", bitrate, "-ar", "48000", "-movflags", "+faststart", str(tmp)])
    tmp.replace(path)
    return integrated_lufs(path)


def log(msg: str) -> None:
    print(f"• {msg}", file=sys.stderr)
