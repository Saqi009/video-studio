"""Command line: ./vs <command> ...   (run ./vs --help)"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

from . import epidemic, graphics, pipeline, tella
from .config import PROJECTS, ROOT, StudioError, load_env, load_settings, project_dir

PROJECT_TEMPLATE = '''# Settings for this video. Anything in studio.toml (brand, output, audio...) can be overridden here.
title = "{title}"
subtitle = ""

[source]
# Leave empty to use every video in source/ in filename order (that is where `./vs tella pull` saves).
files = []

[edit]
style = "talk"               # talk = talking-head edit | beat = cut to the music's beat (or use: build --beat)
trim_silence = true          # cut dead air
silence_threshold_db = -35   # quieter than this counts as silence; raise towards -30 for noisy rooms
min_silence = 0.7            # only pauses longer than this (seconds) are cut
keep_pad = 0.18              # seconds of breathing room kept either side of speech

[graphics]
intro = true
outro = true
outro_cta = "Thanks for watching"
outro_subtext = ""

# Lower thirds: `at` is seconds into the ORIGINAL recording (before silence trimming).
# [[graphics.lower_thirds]]
# at = 2.0
# duration = 5
# name = "Your Name"
# role = "Your role"

[music]
# Leave empty to use the first audio file in music/ (where `./vs music get` saves).
file = ""

[beat]
# Only used when style = "beat" (or build --beat).
pattern = [4, 4, 2, 2, 4, 4, 8]   # beats per cut, repeated. Smaller = faster, more energetic cuts
zoom = 0.06                       # slow zoom on each clip (0 = off)
clip_audio = 0.0                  # 0 = music only; 0.3 = keep the clips' own sound quietly
order = "sequence"                # sequence = clips in filename order; mix = interleave clips
max_seconds = 0                   # 0 = as long as the music allows, e.g. 60 for a one-minute reel

[captions]
mode = "none"     # none | transcribe (local Whisper) | file (an .srt timed to the original recording)
file = ""
burn = false      # true = draw captions into the picture; an .srt sidecar is written either way
'''


def cmd_init(a):
    slug = re.sub(r"[^a-z0-9-]+", "-", a.slug.lower()).strip("-")
    p = PROJECTS / slug
    if (p / "project.toml").exists():
        raise StudioError(f"Project '{slug}' already exists at {p}")
    for d in ("source", "music"):
        (p / d).mkdir(parents=True, exist_ok=True)
    (p / "project.toml").write_text(PROJECT_TEMPLATE.format(title=(a.title or slug.replace("-", " ").title()).replace('"', '\\"')))
    print(f"Created {p}\n  1. add footage:  ./vs tella pull {slug} <tella-video-id>   (or copy files into source/)\n"
          f"  2. add music:    ./vs music search \"calm acoustic\"  then  ./vs music get {slug} <track-id>\n"
          f"  3. edit {p / 'project.toml'}\n  4. ./vs build {slug} --draft   then   ./vs build {slug}")


def cmd_list(a):
    for p in sorted(PROJECTS.glob("*/project.toml")):
        cfg = load_settings(p.parent)
        outs = list((p.parent / "output").glob("*.mp4"))
        print(f"{p.parent.name:28} {cfg.get('title', ''):40} {'built' if outs else '-'}")


def cmd_build(a):
    pipeline.build(project_dir(a.slug), draft=a.draft, style="beat" if a.beat else None)


def cmd_graphics(a):
    p = project_dir(a.slug)
    cfg = load_settings(p)
    bv = graphics.brand_vars(cfg["brand"])
    fps = int(cfg["output"]["fps"])
    out = p / "build" / "graphics"
    print(graphics.render("intro", {**bv, "title": cfg.get("title", a.slug), "subtitle": cfg.get("subtitle", "")}, out, fps))
    print(graphics.render("outro", {**bv, "cta": cfg["graphics"]["outro_cta"], "subtext": cfg["graphics"]["outro_subtext"]}, out, fps))


def cmd_tella_videos(a):
    for v in tella.list_videos(a.limit):
        print(f"{v.get('id', ''):28} {v.get('name') or v.get('title') or ''}")


def cmd_tella_pull(a):
    files = tella.pull(a.video_id, project_dir(a.slug), a.resolution, a.granularity, a.subtitles, a.fps)
    for f in files:
        print(f)


def cmd_music_search(a):
    extra = dict(kv.split("=", 1) for kv in a.param)
    tracks = epidemic.search(" ".join(a.term), a.limit, extra)
    if not tracks:
        print("No tracks found.")
    for t in tracks:
        secs = f"{int(t['seconds'] // 60)}:{int(t['seconds'] % 60):02}" if t["seconds"] else "  ?  "
        print(f"{t['id']:24} {secs:>6}  {str(t['bpm'] or ''):>4} bpm  {t['title'][:36]:36}  {t['artist'][:24]:24}  {t['moods'][:40]}")


def cmd_music_get(a):
    print(epidemic.download_track(a.track_id, project_dir(a.slug), a.format, a.quality))


def cmd_doctor(a):
    ok = True

    def check(label, good, hint=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  {'✓' if good else '✗'} {label}" + ("" if good else f"  -> {hint}"))

    check("ffmpeg", shutil.which("ffmpeg"), "install FFmpeg (brew install ffmpeg / apt install ffmpeg)")
    check("ffprobe", shutil.which("ffprobe"), "comes with FFmpeg")
    node = shutil.which("node")
    major = int(subprocess.run(["node", "-p", "process.versions.node.split('.')[0]"], capture_output=True, text=True).stdout or 0) if node else 0
    check(f"Node.js 22+ (found {major or 'none'})", major >= 22, "install Node.js 22 or newer")
    check("HyperFrames", (ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs").exists(), "run: npm install")
    check("TELLA_API_KEY in .env", os.environ.get("TELLA_API_KEY"), "needed only for ./vs tella ...")
    check("EPIDEMIC_API_KEY in .env", os.environ.get("EPIDEMIC_API_KEY"), "needed only for ./vs music ...")
    print("All good." if ok else "Fix the ✗ items above (the API keys are optional if you add files by hand).")


def main(argv=None):
    load_env()
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a code page without ✓ / ✗ / •
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="vs", description="Video Studio: Tella -> FFmpeg + HyperFrames + Epidemic Sound -> MP4")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a new video project"); s.add_argument("slug"); s.add_argument("--title"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("list", help="list projects"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("doctor", help="check tools and keys"); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("build", help="edit + render the final video"); s.add_argument("slug")
    s.add_argument("--draft", action="store_true", help="fast 720p preview")
    s.add_argument("--beat", action="store_true", help="music-video style: cuts and transitions on the music's beat")
    s.set_defaults(fn=cmd_build)
    s = sub.add_parser("graphics", help="render only the intro/outro to preview the look"); s.add_argument("slug"); s.set_defaults(fn=cmd_graphics)

    t = sub.add_parser("tella", help="Tella recordings").add_subparsers(dest="tcmd", required=True)
    s = t.add_parser("videos", help="list your Tella videos"); s.add_argument("--limit", type=int, default=20); s.set_defaults(fn=cmd_tella_videos)
    s = t.add_parser("pull", help="export a Tella video into a project's source/")
    s.add_argument("slug"); s.add_argument("video_id")
    s.add_argument("--resolution", default="1080p", choices=["1080p", "4k"])
    s.add_argument("--granularity", default="video", choices=["video", "clips", "raw"],
                   help="video = Tella's edited video; clips = one file per clip; raw = original uploads")
    s.add_argument("--subtitles", action="store_true", help="ask Tella to include subtitles in the export")
    s.add_argument("--fps", type=int, help="e.g. 60 (may need a paid Tella plan)")
    s.set_defaults(fn=cmd_tella_pull)

    m = sub.add_parser("music", help="Epidemic Sound music").add_subparsers(dest="mcmd", required=True)
    s = m.add_parser("search", help="search tracks"); s.add_argument("term", nargs="+"); s.add_argument("--limit", type=int, default=10)
    s.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="extra API filter, e.g. --param bpmMin=90")
    s.set_defaults(fn=cmd_music_search)
    s = m.add_parser("get", help="download a track into a project's music/"); s.add_argument("slug"); s.add_argument("track_id")
    s.add_argument("--format", default="mp3", choices=["mp3", "wav"]); s.add_argument("--quality", default="high", choices=["normal", "high"])
    s.set_defaults(fn=cmd_music_get)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except StudioError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
