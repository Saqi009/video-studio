"""Render the HyperFrames templates (intro, outro, lower thirds) with this video's text and brand."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .config import ROOT, TEMPLATES, StudioError


def _hyperframes() -> list[str]:
    # Run the CLI's JS entry with node directly: identical on Windows, macOS and Linux.
    local = ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    node = shutil.which("node")
    if local.exists() and node:
        return [node, str(local)]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "hyperframes"]
    raise StudioError("HyperFrames is not installed. Run: npm install   (needs Node.js 22+)")


def render(template: str, variables: dict, out_dir: Path, fps: int, fmt: str = "mp4",
           duration_override: float | None = None) -> Path:
    """Render templates/<template> to out_dir; reuses a previous render with identical inputs."""
    src = TEMPLATES / template
    if not (src / "index.html").exists():
        raise StudioError(f"Missing template: {src}/index.html")
    html = (src / "index.html").read_text()
    key = hashlib.sha1(json.dumps([html, variables, fps, fmt, duration_override], sort_keys=True).encode()).hexdigest()[:10]
    out = out_dir / f"{template}-{key}.{fmt}"
    if out.exists():
        return out

    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / f".{template}-{key}"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(src, work)
    if duration_override is not None:  # composition length is static HTML, so rewrite it in the copy
        d = f'data-duration="{duration_override:g}"'
        (work / "index.html").write_text(html.replace('data-duration="5"', d))

    cmd = [*_hyperframes(), "render", str(work), "-o", str(out), "--fps", str(fps), "--quality", "delivery",
           "--format", fmt, "--quiet", "--variables", json.dumps(variables)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(work, ignore_errors=True)
    if proc.returncode != 0 or not out.exists():
        raise StudioError(f"HyperFrames render of '{template}' failed:\n{(proc.stdout + proc.stderr)[-2500:]}")
    return out


def brand_vars(brand: dict) -> dict:
    return {"bg": brand["background"], "ink": brand["text"], "accent": brand["accent"],
            "font": brand["font"], "brand": brand.get("name", "")}
