"""Epidemic Sound Partner Content API: search music and download a track into a project.

Docs: https://developers.epidemicsound.com/docs/
  GET /v0/tracks/search?term=...            search tracks
  GET /v0/tracks/{id}/download?format=&quality=   short-lived download link
Auth (server-side API key): Authorization: Bearer <key>, plus x-partner-user-id.
Which tracks you can download depends on your Epidemic Sound agreement.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .config import StudioError, need_env
from .http import download, find_key, find_list, request_json


def _base() -> str:
    return os.environ.get("EPIDEMIC_API_BASE", "https://partner-content-api.epidemicsound.com").rstrip("/")


def _headers() -> dict:
    key = need_env("EPIDEMIC_API_KEY", "Get a Partner API key from Epidemic Sound and put it in .env.")
    headers = {"Authorization": f"Bearer {key}"}
    user = os.environ.get("EPIDEMIC_USER_ID", "").strip()
    if user:
        headers["x-partner-user-id"] = user
    return headers


def _seconds(track: dict) -> float | None:
    for k in ("length", "duration", "durationSeconds"):
        v = track.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    ms = track.get("durationMs") or track.get("lengthMs")
    return ms / 1000 if isinstance(ms, (int, float)) else None


def _names(v) -> str:
    if isinstance(v, list):
        return ", ".join(x.get("name", "") if isinstance(x, dict) else str(x) for x in v)
    return str(v or "")


def summarize(track: dict) -> dict:
    artists = track.get("mainArtists") or track.get("artists") or track.get("creatives") or track.get("artist")
    return {
        "id": str(track.get("id", "")),
        "title": track.get("title", ""),
        "artist": _names(artists),
        "seconds": _seconds(track),
        "bpm": track.get("bpm"),
        "moods": _names(track.get("moods")),
        "genres": _names(track.get("genres")),
    }


def search(term: str, limit: int = 10, extra: dict | None = None) -> list[dict]:
    params = {"term": term, "limit": limit, **(extra or {})}
    data = request_json("GET", f"{_base()}/v0/tracks/search", _headers(), params=params)
    return [summarize(t) for t in find_list(data, "tracks", "data", "items", "results") if isinstance(t, dict)]


def download_track(track_id: str, project: Path, fmt: str = "mp3", quality: str = "high") -> Path:
    data = request_json("GET", f"{_base()}/v0/tracks/{track_id}/download", _headers(),
                        params={"format": fmt, "quality": quality})
    link = find_key(data, "url", "downloadUrl", "link")
    if not link:
        raise StudioError(f"Epidemic Sound returned no download link: {data}")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", track_id)
    return download(str(link), project / "music" / f"epidemic-{safe}.{fmt}")
