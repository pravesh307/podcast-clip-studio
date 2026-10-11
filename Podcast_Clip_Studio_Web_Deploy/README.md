# Podcast Clip Studio

A **real, local-first** web app that takes a Spotify episode URL plus a **separately obtained video you have rights to process**, transcribes the episode, discovers 3–4-minute passages, and renders **1920 × 1080, 16:9** MP4 clips with titles and thumbnail JPGs.

**Important:** Spotify's oEmbed API provides an episode title/cover, **not** its downloadable video. This project intentionally does **not** scrape Spotify, defeat DRM, stream-rip, or record protected playback. It cannot convert an arbitrary Spotify URL into an MP4 without access to a usable, permitted full-video file. The Spotify URL helps label and organise the results.

## Quick start (Mac)

1. Install **Python 3.11 or 3.12** and **FFmpeg**. With [Homebrew](https://brew.sh/): `brew install ffmpeg python@3.12`.
2. Unzip, open **Terminal**, `cd` into the `podcast-clip-studio` folder.
3. Run `python3.12 -m venv .venv && source .venv/bin/activate` (use `python3.11` if that is your installed version).
4. Run `pip install -r requirements.txt`.
5. For *context-aware AI* highlights, set your own [OpenAI API key](https://platform.openai.com/api-keys): `export OPENAI_API_KEY='YOUR_KEY'` (or skip this and use **Draft cuts**). Using the API costs money, separate from ChatGPT subscriptions; the transcript is sent to the API in smart mode.
6. Run `python app.py`, and open **http://127.0.0.1:8765**.
7. Paste your Spotify episode URL, attach an MP4/MOV/MKV/WEBM/M4V recording that you can legally process, and click **Generate clips**.
8. Review the generated videos/thumbnails, preview results in the browser, download individual files or the ZIP. Completed project history stays available after restarting the app.

Or, on Mac, double-click `start_mac.command` after installing Python and FFmpeg. If macOS blocks this unverified shell script, use the Terminal instructions instead.

### How good are the results?

- **Smart highlights (AI)** — Requires `OPENAI_API_KEY`. Examines the entire transcript in overlapping 15-minute windows, scores context-aware 180–240-second moments, filters poor/overlapping clips, writes truthful proposed titles. Defaults to `gpt-4.1-mini` and is configurable through `OPENAI_CLIP_MODEL`. It won't necessarily find every usable clip; editorial review is recommended.
- **Draft cuts (offline selection)** — No API key. Divides the transcript into rough 3–4-minute drafts at natural transcript boundaries, using simple topic titles. Not smart enough to assess context or quality on its own.
- Both use `faster-whisper` locally for transcription (downloads a free model on first use), FFmpeg to render 1080p landscape MP4 (source resolution may be lower, in which case upscaling cannot create detail), and Pillow to build 1280 × 720 thumbnail JPGs from the *actual footage*.
- **Performance:** Transcription and especially exporting dozens of 1080p videos can take substantial time. A multi-hour video needs disk space, processor time, and optionally AI API budget. Processing is queued one episode at a time.

### Safety and privacy

- Runs on `127.0.0.1` only. Do **not** port-forward or publicly expose it; there's no user authentication.
- Uploaded files and generated clips stay in `data/` on your machine. You can delete that folder to remove them.
- Only Spotify's official *metadata* endpoint is called for episode info. No Spotify stream is accessed.
- Local transcription works offline after models are downloaded. Smart highlight selection sends transcript text to OpenAI if enabled; audio/video stays on your machine.
- You are responsible for rights to any footage you process or publish, including on YouTube.
- Thumbnails use real still frames with title overlays. They are not fabricated photos of speakers.

### Outputs

`data/<job_id>/clips/` contains `transcript.json`, `manifest.json`, `clips.csv`, and a folder for each clip containing `video.mp4`, `thumbnail.jpg`, `title.txt`, and `metadata.json`.

### Common issues

- **Missing ffmpeg/ffprobe:** `brew install ffmpeg`.
- **Transcription model taking time:** first use downloads the Whisper model; change model to `base` for low-powered Macs.
- **Smart mode rejected:** set `OPENAI_API_KEY` and restart server, or select Draft cuts.
- **Source has no audio:** AI cannot discover passages without dialogue; FFmpeg may render silent files.
- **No qualifying excerpts:** smart mode is deliberately selective, and may find none in a particular transcript; Draft cuts are an alternative.

## Development / tests

`python -m pytest -q tests` (requires `pip install pytest`).


## Live website on an iPhone (Render deployment)

The app can run as a **private HTTPS website** with the included `Dockerfile` and `render.yaml`.

1. Push these project files to a GitHub repository that the Render account can access.
2. In Render, choose **New > Blueprint**, connect your repository, and deploy `render.yaml`.
3. Set a **strong** `APP_PASSWORD` at deployment; this is REQUIRED, otherwise the app returns 503 instead of showing an unprotected uploader. Username is always `studio`.
4. Optionally add your **OpenAI API key** as an environment variable named `OPENAI_API_KEY` in the Render dashboard to enable genuine AI-highlight selection; do not commit API keys to GitHub. You can use Draft mode without an API key.
5. Once Render reports a healthy service, open its HTTPS service URL in Safari on your iPhone. Your browser asks for username `studio` and your chosen password.

**Cost and limitations:** The included Blueprint explicitly uses a paid 2 GB RAM web service and 30 GB persistent disk; review current Render pricing and approve charges before deploying. Full-length 1080p uploads and rendering may exceed basic instance CPU, memory, disk and request-timeout limits; no clips or uptime are guaranteed on low-resource hosting. The server processes uploaded video remotely, unlike local mode. Project history is stored on its persistent disk; in-progress jobs do not survive server restarts. This code has not been deployed publicly. The Spotify URL is metadata only and does not provide video, and there is no protected-stream extraction or DRM bypass.
