"""Tella Public API: list videos, export a video, download the export into a project.

Docs: https://www.tella.com/docs/introduction
  POST /v1/videos/{id}/exports           start an export (granularity, resolution, subtitles)
  GET  /v1/videos/{id}/exports/{exportId} poll: status, progress, downloadUrl (null until done)
"""
from __future__ import annotations

import os
import sys
import time
import zipfile
from pathlib import Path

from .config import StudioError, need_env
from .http import download, find_key, find_list, request_json

DONE = {"completed", "complete", "ready", "done", "succeeded", "success"}
FAILED = {"failed", "error", "errored", "cancelled", "canceled"}


def _base() -> str:
    return os.environ.get("TELLA_API_BASE", "https://api.tella.com/v1").rstrip("/")


def _headers() -> dict:
    key = need_env("TELLA_API_KEY", "Create an API key in Tella (Settings -> API) and put it in .env.")
    return {"Authorization": f"Bearer {key}"}


def list_videos(limit: int = 20) -> list[dict]:
    data = request_json("GET", f"{_base()}/videos", _headers(), params={"limit": limit})
    return find_list(data, "videos", "data", "items", "results")


def video_info(video_id: str) -> dict:
    return request_json("GET", f"{_base()}/videos/{video_id}", _headers())


def start_export(video_id: str, resolution: str, granularity: str, subtitles: bool, fps: int | None) -> str:
    body: dict = {"granularity": granularity, "resolution": resolution, "subtitles": subtitles}
    if fps:
        body["fps"] = fps
    data = request_json("POST", f"{_base()}/videos/{video_id}/exports", _headers(), body=body)
    export_id = find_key(data, "exportId", "export_id", "id")
    if not export_id:
        raise StudioError(f"Tella did not return an export id: {data}")
    return str(export_id)


def wait_for_export(video_id: str, export_id: str, timeout_s: int = 1800) -> str:
    url = f"{_base()}/videos/{video_id}/exports/{export_id}"
    start, delay = time.time(), 3.0
    while True:
        data = request_json("GET", url, _headers())
        status = str(find_key(data, "status") or "").lower()
        progress = find_key(data, "progress")
        link = find_key(data, "downloadUrl", "download_url", "url")
        print(f"  Tella export {export_id}: {status or '?'} {progress if progress is not None else ''}", file=sys.stderr)
        if link and (status in DONE or not status):
            return str(link)
        if status in FAILED:
            raise StudioError(f"Tella export failed: {data}")
        if time.time() - start > timeout_s:
            raise StudioError("Timed out waiting for the Tella export. Run the pull again later.")
        time.sleep(delay)
        delay = min(delay * 1.5, 20)


def pull(video_id: str, project: Path, resolution: str = "1080p", granularity: str = "video",
         subtitles: bool = False, fps: int | None = None) -> list[Path]:
    """Export a Tella video and save it in <project>/source/. Returns the saved media files."""
    print(f"Starting Tella export of {video_id} ({granularity}, {resolution})...", file=sys.stderr)
    export_id = start_export(video_id, resolution, granularity, subtitles, fps)
    link = wait_for_export(video_id, export_id)
    ext = Path(link.split("?")[0]).suffix.lower() or ".mp4"
    dest = download(link, project / "source" / f"tella-{video_id}{ext}")
    if ext == ".zip" or zipfile.is_zipfile(dest):
        # clip/raw exports can arrive as an archive of several files
        out_dir = project / "source" / f"tella-{video_id}"
        with zipfile.ZipFile(dest) as z:
            for member in z.namelist():  # refuse paths that escape the folder
                target = (out_dir / member).resolve()
                if not str(target).startswith(str(out_dir.resolve())):
                    raise StudioError(f"Unsafe path in Tella archive: {member}")
            z.extractall(out_dir)
        dest.unlink()
        return sorted(p for p in out_dir.rglob("*") if p.is_file())
    return [dest]
