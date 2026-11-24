# Video Studio

A command-line video editing system that ties four tools together:

| Tool | Job in the system |
| --- | --- |
| **Tella** | Where you record. `./vs tella pull` exports a recording over the Tella API and drops it into a project. |
| **FFmpeg** | The editor. Joins and conforms clips, cuts dead air, cleans the voice, overlays graphics, burns captions, mixes and ducks music, hits −14 LUFS, exports a YouTube-ready MP4. |
| **HyperFrames** | Motion graphics. Intro card, outro/CTA card and lower thirds are HTML templates in `templates/`, rendered to video (lower thirds with transparency). |
| **Epidemic Sound** | Music. `./vs music search` / `./vs music get` find and download a track over the Partner API; the build loops, fades and ducks it under your voice. |

```
Tella recording ──► source/ ──► conform ──► cut silences ──► + lower thirds + captions
                                                                    │
       HyperFrames intro ─────────────────────────────────────►  concat  ◄── HyperFrames outro
                                                                    │
       Epidemic Sound track ──► loop / fade / duck under voice ──► mix ──► loudness ──► output/<name>.mp4
                                                                                          + .srt + thumbnail
```

## Setup (once)

On Windows, follow "Setup on Windows" below instead. Mac/Linux: needs **Python 3.11+**, **Node.js 22+**, **FFmpeg**, and Google Chrome or Chromium (HyperFrames renders through headless Chrome).

```bash
npm install
```

```bash
cp .env.example .env
```

Fill in `.env`:

- `TELLA_API_KEY`: from Tella's API settings.
- `EPIDEMIC_API_KEY` and `EPIDEMIC_USER_ID`: from your Epidemic Sound Partner API access. Which tracks you can download depends on your agreement with Epidemic Sound.

Both keys are optional. You can also drop exported videos into `source/` and downloaded tracks into `music/` by hand.

```bash
./vs doctor
```

Then set your brand colours, font and name in `studio.toml`.

## Setup on Windows 10 / 11

1. Install these (accept the defaults; tick **"Add python.exe to PATH"** in the Python installer):
   - Python 3.11 or newer: https://www.python.org/downloads/
   - Node.js 22 LTS: https://nodejs.org/
   - Google Chrome: https://www.google.com/chrome/
   - Git: https://git-scm.com/download/win
2. Install FFmpeg. Open **PowerShell** and run:

   ```powershell
   winget install --id Gyan.FFmpeg -e
   ```

   Close and reopen PowerShell afterwards so it can find `ffmpeg`.
3. Get the project and install HyperFrames:

   ```powershell
   git clone https://github.com/Saqi009/video-studio.git
   cd video-studio
   npm install
   copy .env.example .env
   notepad .env
   ```

   Paste your keys into Notepad, save and close it.
4. Check everything:

   ```powershell
   .\vs doctor
   ```

On Windows, type `.\vs` wherever this README says `./vs`, for example `.\vs build my-video --draft`.

## Making a video

```bash
./vs init my-video --title "How we ship every Friday"
```

```bash
./vs tella videos
```

```bash
./vs tella pull my-video <tella-video-id>
```

```bash
./vs music search upbeat corporate
```

```bash
./vs music get my-video <track-id>
```

Edit `projects/my-video/project.toml` (title, lower thirds, captions), then:

```bash
./vs build my-video --draft
```

```bash
./vs build my-video
```

Results go to `projects/my-video/output/`: `my-video.mp4`, `my-video.srt` (when captions are on) and `my-video-thumbnail.jpg`.
`build/edit-report.json` lists which parts of the recording were kept.

To check only the look of the intro and outro: `./vs graphics my-video`.

## Beat mode: a music video cut to the beat

For B-roll, travel clips, events or product shots, cut the clips to the music instead of trimming speech:

```bash
./vs build my-video --beat --draft
```

(or set `style = "beat"` under `[edit]` in `project.toml`).

- HyperFrames' beat detector finds the beats in the track in `music/`.
- Every cut lands exactly on a beat, every 4, 4, 2, 2, 4, 4, 8 beats (`[beat] pattern`). Use smaller numbers for faster, more energetic cuts.
- Most cuts are hard cuts. Every third one is an effect (white flash, zoom, slide, circle, smooth slide, fade), centred on the beat.
- The music is shifted so a beat lands exactly where the intro card ends.
- Each clip gets a slow zoom in or out (`zoom`). Clips fill the frame, and vertical phone clips in a 16:9 video sit over a blurred copy of themselves (`fit = "auto"`).
- Picks are spread from the start to the end of every clip, in filename order (`order = "mix"` interleaves the clips instead). Rename clips `01-...`, `02-...` to set the story order.
- `clip_audio = 0.3` keeps the clips' own sound quietly under the music. `max_seconds = 60` makes a one-minute reel.

`build/edit-report.json` lists every cut time, transition and which part of which clip was used.

## What the build does

1. **Conform.** Every file in `source/` (in filename order) is scaled and padded to the output size and frame rate, with 48 kHz stereo audio, and joined. With `voice_cleanup` on, it also gets a rumble filter, light denoise and gentle compression.
2. **Cut dead air.** Pauses longer than `min_silence` and quieter than `silence_threshold_db` are removed. `keep_pad` seconds of room are kept either side of speech, and every cut gets a 10 ms audio fade so it doesn't click.
3. **Captions.** Set `mode = "transcribe"` for local Whisper through `hyperframes transcribe` (the model downloads on first use). Set `mode = "file"` to use your own `.srt`, timed to the original recording; the build remaps it through the cuts. Set `burn = true` to draw captions into the picture. The `.srt` sidecar is written either way.
4. **Graphics.** HyperFrames renders the intro, outro and each lower third from `templates/`, using your brand values. Renders are cached in `build/graphics/` and only redone when text or style changes.
5. **Mix and export.** Voice is levelled to −18 LUFS. Music is looped to length, set to `music_lufs`, faded, and side-chain ducked while you talk. The mix is normalised to `target_lufs` (−14) with a −1.5 dBTP ceiling. Video is H.264 High, BT.709 tags, `+faststart`.

## Settings

`studio.toml` holds the house style for all videos: brand, output size, fps and quality, and audio levels.
`projects/<name>/project.toml` holds this video's settings, and can override any `studio.toml` section.

- For vertical shorts, set `[output] width = 1080, height = 1920`. The templates are designed for 16:9, so adjust their CSS for a vertical look.
- If music is too loud or quiet, change `music_lufs` (for example −24 is louder, −32 is quieter).
- If cuts feel too tight, raise `keep_pad` or `min_silence`. If background noise isn't being cut, raise `silence_threshold_db` towards −30.
- Lower-third `at` times are in seconds of the **original recording**, so they stay attached to the right moment when the silence cutting changes.

## Customising the graphics

Each folder in `templates/` is a HyperFrames project: plain HTML/CSS with GSAP animation and `data-composition-variables` for the text and colours the build fills in.
Preview and tweak one live:

```bash
npx hyperframes preview templates/intro
```

The HyperFrames catalog (`npx hyperframes catalog`) has ~450 ready-made blocks (transitions, kinetic type, charts) you can pull into a template.
With Claude Code, `npx skills add heygen-com/hyperframes` installs the HyperFrames authoring skills.

## API notes

- **Tella** (`studio/tella.py`): `POST /v1/videos/{id}/exports` starts an export; `GET /v1/videos/{id}/exports/{exportId}` is polled until the `downloadUrl` is ready. `--granularity clips|raw` exports one file per clip; archives are unzipped into `source/`.
- **Epidemic Sound** (`studio/epidemic.py`): `GET /v0/tracks/search?term=` and `GET /v0/tracks/{id}/download?format=&quality=`, with `Authorization: Bearer <key>` and `x-partner-user-id`. Extra search filters go through `--param KEY=VALUE`. Set `EPIDEMIC_API_BASE` if your account uses the newer API host.

Responses are read tolerantly (for example `{"export": {...}}` or a bare object), so small API changes don't break the pipeline. If a call fails, the error shows the HTTP status and the API's message.
