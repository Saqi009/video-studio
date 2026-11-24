"""Paths, settings files and secrets."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
PROJECTS = ROOT / "projects"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


class StudioError(RuntimeError):
    """An error with a message meant for the user (no traceback)."""


def load_env() -> None:
    """Read KEY=value lines from .env into os.environ (real env vars win)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def need_env(name: str, hint: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise StudioError(f"{name} is not set. {hint}")
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


DEFAULTS: dict = {
    "brand": {
        "name": "",
        "font": "Inter, Helvetica, Arial, sans-serif",
        "background": "#0b0d12",
        "text": "#ffffff",
        "accent": "#5b8cff",
    },
    "output": {"width": 1920, "height": 1080, "fps": 30, "crf": 18, "preset": "medium", "audio_bitrate": "192k"},
    "audio": {
        "target_lufs": -14.0,
        "true_peak": -1.5,
        "voice_cleanup": True,
        "music_lufs": -28.0,
        "duck": True,
        "music_fade_in": 1.5,
        "music_fade_out": 3.0,
    },
    "edit": {"trim_silence": True, "silence_threshold_db": -35.0, "min_silence": 0.7, "keep_pad": 0.18},
    "graphics": {
        "intro": True, "outro": True,
        "outro_cta": "Thanks for watching", "outro_subtext": "",
        "lower_thirds": [],
    },
    "captions": {"mode": "none", "file": "", "burn": False, "language": "en", "model": "small.en", "font_size": 22},
    "source": {"files": []},
    "music": {"file": ""},
}


def load_settings(project_dir: Path) -> dict:
    """DEFAULTS <- studio.toml (house style) <- project.toml (this video)."""
    settings = DEFAULTS
    for f in (ROOT / "studio.toml", project_dir / "project.toml"):
        if f.exists():
            try:
                settings = _deep_merge(settings, tomllib.loads(f.read_text()))
            except tomllib.TOMLDecodeError as e:
                raise StudioError(f"{f.name}: {e}") from e
    return settings


def project_dir(slug: str) -> Path:
    p = PROJECTS / slug
    if not (p / "project.toml").exists():
        raise StudioError(f"No project '{slug}'. Create it with: ./vs init {slug}")
    return p
