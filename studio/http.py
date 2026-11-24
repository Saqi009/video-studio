"""Tiny JSON-over-HTTPS helper (stdlib only) shared by the Tella and Epidemic Sound clients."""
from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .config import StudioError

UA = "video-studio/1.0"


def request_json(method: str, url: str, headers: dict, params: dict | None = None, body: Any = None) -> Any:
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
    data = None
    headers = {"Accept": "application/json", "User-Agent": UA, **headers}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise StudioError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise StudioError(f"{method} {url.split('?')[0]} failed: {e.reason}") from e
    return json.loads(raw) if raw.strip() else {}


def download(url: str, dest: Path, headers: dict | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total and sys.stderr.isatty():
                    print(f"\r  downloading {dest.name}: {done * 100 // total}%", end="", file=sys.stderr)
        if total and sys.stderr.isatty():
            print(file=sys.stderr)
    except urllib.error.URLError as e:
        tmp.unlink(missing_ok=True)
        raise StudioError(f"Download failed ({dest.name}): {e}") from e
    shutil.move(tmp, dest)
    return dest


def find_key(obj: Any, *names: str) -> Any:
    """First value for any of `names`, searched depth-first through nested dicts/lists.

    The vendor APIs wrap payloads differently between endpoints and versions
    ({"export": {...}} vs a bare object), so lookups go through this instead of fixed paths.
    """
    if isinstance(obj, dict):
        for n in names:
            if n in obj and obj[n] not in (None, ""):
                return obj[n]
        children: Iterable = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = find_key(child, *names)
        if found not in (None, ""):
            return found
    return None


def find_list(obj: Any, *names: str) -> list:
    """The first list under any of `names`, or the object itself if it is a list."""
    if isinstance(obj, list):
        return obj
    v = find_key(obj, *names)
    return v if isinstance(v, list) else []
