# Podcast Clip Studio

A private web app for turning accessible long-form recordings into 1080p landscape clips with titles and thumbnails.

## Workflow

1. Paste a **YouTube video link**. The server downloads accessible recordings; you do not need to download/upload a file yourself.
2. For a **Spotify episode link**, click Look up and confirm a matching YouTube recording. Spotify metadata does not include downloadable episode video. If there is no YouTube version, a Spotify-only link cannot supply a video to edit. Optional file upload remains available.
3. Choose a target length from **3 to 15 minutes**. Cuts can vary by up to 20 seconds to finish a sentence naturally, but always remain within the 3–15 minute range.
4. Choose Smart AI or Draft cuts and the creative direction: Funny, Curiosity, or Insight.
5. Smart AI needs an OpenAI API key: set `OPENAI_API_KEY` on the server or enter a key for that job. Per-job keys are held in memory and never written to job records, manifests, or browser storage. Transcripts and thumbnail candidate frames are sent to OpenAI; API usage has separate billing.
6. Review and download the MP4 clips, JPG thumbnails, alternate titles, and ZIP archive.

Smart AI reviews overlapping transcript windows, picks complete stories with setup and payoff, avoids repetitive clips, and proposes relevant titles and short captions. It also compares three real video frames per clip to choose a thumbnail image. It cannot guarantee that a source is funny or that a title will achieve a particular click-through rate. Draft mode makes rough timed cuts without semantic or visual AI.

## Hosting

The existing Render service builds from root directory `Podcast_Clip_Studio_Web_Deploy`, Dockerfile `./Dockerfile`, context `.`. The Docker image installs FFmpeg, Node 22 for yt-dlp, Python dependencies, and fonts.

- `APP_USERNAME`: default `pravesh`.
- `APP_PASSWORD`: required on hosted deployments. Configure privately in Render; never commit passwords.
- `OPENAI_API_KEY`: optional server default; otherwise enter a per-job key for Smart AI.
- `OPENAI_CLIP_MODEL`: default `gpt-4.1-mini` (must support JSON output and image input).
- `DATA_DIR`: default `/app/data` in Docker. Persistent storage is needed for durable history.
- `MAX_UPLOAD_GB`: upload/download size limit, default 12 GB.
- `/health`: unauthenticated health check. Other routes and output files require the workspace login.

The current free Render service can serve the interface, but full podcast transcription and 1080p rendering may exceed its memory and CPU limits. It sleeps when idle and its files do not persist across redeploys/restarts. The included Blueprint describes a paid service and persistent disk; creating that Blueprint may incur charges. This update does not upgrade the existing service.

YouTube sometimes blocks cloud-server downloads or requires authentication. The app does not bypass sign-in, age restrictions, DRM, or provider blocks. It reports failures and offers an optional file upload. Clips require permission to process and publish the source.

One job runs at a time. Completed outputs are retained; the original source file is removed after processing to reduce disk use. In-progress jobs do not survive restarts. Download completed ZIPs to keep your work.

## Local development

Install Python 3.11+, FFmpeg and Node 22+. Run `pip install -r requirements.txt`, then `python app.py` and visit `http://127.0.0.1:8765`. API-backed mode requires your own key. Run tests with `python -m pytest -q tests` after installing pytest and httpx. Tests mock provider calls; they do not claim a live YouTube download or paid OpenAI request succeeded.
