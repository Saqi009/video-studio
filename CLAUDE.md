# Video Studio — notes for Claude

CLI video pipeline: Tella (recordings) → FFmpeg (edit/mix/export) + HyperFrames (graphics) + Epidemic Sound (music). See README.md.

- Entry point: `./vs` → `studio/__main__.py`. Python stdlib only; do not add pip dependencies.
- Pipeline: `studio/pipeline.py` (`build`). FFmpeg helpers: `studio/ff.py`. HyperFrames renders: `studio/graphics.py`.
- Settings: `studio.toml` (house style) deep-merged with `projects/<slug>/project.toml`; defaults in `studio/config.py` `DEFAULTS`.
- Templates in `templates/<name>/index.html` are HyperFrames compositions; text/colours come in via `data-composition-variables` and `window.__hyperframes.getVariables()`. The lower-third's length is static HTML, so `graphics.render(..., duration_override=)` rewrites `data-duration="5"` in a temp copy — keep that literal in the template.
- When the user asks for an edit change, prefer changing `project.toml`, then `./vs build <slug> --draft`, then check frames with `ffmpeg -ss <t> -i output/<slug>-draft.mp4 -frames:v 1 frame.png`.
- Secrets live in `.env` (git-ignored). Never print or commit them.
