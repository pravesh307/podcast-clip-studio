"""Private local web app for automatic highlights from user-provided podcast footage.

Run from this directory: python app.py
"""
from __future__ import annotations

import os
import base64
import hmac
import re
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sources import parse_source, source_info, find_youtube_versions, download_youtube

from engine import render_job

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv("DATA_DIR", str(BASE / "data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
APP = FastAPI(title="Podcast Clip Studio", docs_url=None, redoc_url=None)
APP.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
EXECUTOR = ThreadPoolExecutor(max_workers=1)
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
MAX_UPLOAD_BYTES = int(float(os.getenv("MAX_UPLOAD_GB", "12")) * 1024**3)
SPOTIFY_EPISODE_RE = re.compile(r"^/episode/([A-Za-z0-9]{10,32})/?$")


@APP.middleware("http")
async def protect_private_workspace(request: Request, call_next):
    # On hosted deployments, protect ALL routes and video downloads with HTTP Basic Auth.
    # Render terminates HTTPS before requests reach the container.
    if request.url.path == "/health":
        return await call_next(request)
    require_auth = os.getenv("PUBLIC_DEPLOY", "0") == "1"
    password = os.getenv("APP_PASSWORD", "")
    if require_auth and not password:
        return JSONResponse({"detail": "Set APP_PASSWORD before allowing hosted access."}, status_code=503)
    if password:
        value = request.headers.get("authorization", "")
        permitted = False
        if value.startswith("Basic "):
            try:
                decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
                user, supplied = decoded.split(":", 1)
                permitted = hmac.compare_digest(user, os.getenv("APP_USERNAME", "pravesh")) and hmac.compare_digest(supplied, password)
            except (UnicodeDecodeError, ValueError):
                pass
        if not permitted:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Podcast Clip Studio"', "Cache-Control": "no-store"})
    return await call_next(request)


@APP.get("/health")
def health():
    return {"status": "ok"}


def restore_completed_jobs() -> None:
    """Finished jobs remain available after restarting the local server."""
    for folder in DATA.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"[a-f0-9]{14}", folder.name):
            continue
        manifest_path = folder / "clips" / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            import json
            manifest = json.loads(manifest_path.read_text(encoding="utf8"))
            JOBS[folder.name] = {
                "job_id": folder.name,
                "status": "complete",
                "progress": 100,
                "message": "Completed project restored from disk",
                "episode": {"url": manifest.get("spotify_url", ""), "title": manifest.get("episode", "Podcast episode")},
                "manifest": manifest,
            }
        except (OSError, ValueError):
            continue


restore_completed_jobs()


def parse_episode_id(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        raise ValueError("Invalid Spotify URL")
    if parsed.scheme != "https" or parsed.hostname != "open.spotify.com":
        raise ValueError("Enter an https://open.spotify.com/episode/... URL")
    match = SPOTIFY_EPISODE_RE.match(parsed.path)
    if not match:
        raise ValueError("This must be a Spotify episode link, not an artist or playlist")
    return match.group(1)


def episode_info(url: str) -> dict:
    return source_info(url)


@APP.get("/api/config")
def config():
    return {"ai_configured": bool(os.getenv("OPENAI_API_KEY")), "min_minutes": 3, "max_minutes": 15,
            "max_upload_gb": round(MAX_UPLOAD_BYTES / 1024**3, 1)}


@APP.get("/api/source-candidates")
def source_candidates(url: str):
    try:
        meta = source_info(url)
        if meta["platform"] != "spotify":
            return {"candidates": []}
        if meta.get("note"):
            raise ValueError("Spotify could not return the episode title. Paste its YouTube video link instead.")
        return {"candidates": find_youtube_versions(meta["title"])}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@APP.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@APP.get("/api/episode")
def api_episode(url: str):
    try:
        return episode_info(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _update_job(job_id: str, message: str, progress: int):
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id]["message"] = message
            JOBS[job_id]["progress"] = progress


def _run_job(job_id: str, video: Path | None, output: Path, url: str, title: str,
             whisper_model: str, selection_mode: str, clip_minutes: int, style: str,
             download_url: str, api_key: str):
    with LOCK:
        JOBS[job_id]["status"] = "working"
    try:
        if video is None:
            video, title = download_youtube(download_url, output.parent,
                                           lambda msg, pct: _update_job(job_id, msg, pct), MAX_UPLOAD_BYTES)
            with LOCK:
                JOBS[job_id]["episode"]["title"] = title
        manifest = render_job(video, output, url, title, whisper_model, selection_mode,
                              os.getenv("OPENAI_CLIP_MODEL", "gpt-4.1-mini"),
                              lambda message, progress: _update_job(job_id, message, 15 + round(progress * .85)),
                              clip_minutes=clip_minutes, style=style, api_key=api_key)
        with LOCK:
            JOBS[job_id].update(status="complete", manifest=manifest, progress=100, message="All clips are ready")
    except Exception as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[redacted]")
        with LOCK:
            JOBS[job_id].update(status="failed", message=message[:1200])
    finally:
        api_key = ""
        # Keep exported clips, release the much larger original recording.
        if video is not None:
            video.unlink(missing_ok=True)


@APP.post("/api/jobs", status_code=202)
async def create_job(source_url: str = Form(""), spotify_url: str = Form(""),
                     youtube_url: str = Form(""), video: UploadFile | None = File(None),
                     mode: str = Form("smart"), whisper: str = Form("base"),
                     clip_minutes: int = Form(4), style: str = Form("funny"),
                     api_key: str = Form(""), rights_confirmed: bool = Form(False)):
    if not rights_confirmed:
        raise HTTPException(status_code=422, detail="Confirm permission to process this recording.")
    if not 3 <= clip_minutes <= 15:
        raise HTTPException(status_code=422, detail="Choose a clip length between 3 and 15 minutes.")
    if mode not in {"smart", "draft"} or style not in {"funny", "curiosity", "insight"}:
        raise HTTPException(status_code=422, detail="Unknown selection mode or title style.")
    if whisper not in {"base", "small", "medium"}:
        raise HTTPException(status_code=422, detail="Unknown transcription size.")
    api_key = api_key.strip() or os.getenv("OPENAI_API_KEY", "")
    if mode == "smart" and not api_key:
        raise HTTPException(status_code=422, detail="Add your OpenAI API key below to use Smart AI, or select Draft cuts.")
    try:
        source = parse_source(source_url or spotify_url) if (source_url or spotify_url) else None
        if source is None and video is None:
            raise ValueError("Paste a YouTube video or Spotify episode link.")
        download_url = source["url"] if source else ""
        if video is None and source["platform"] == "spotify":
            if not youtube_url:
                raise ValueError("Spotify does not supply video downloads. Click Look up and select its YouTube version, or paste that YouTube link.")
            candidate = parse_source(youtube_url)
            if candidate["platform"] != "youtube":
                raise ValueError("The matching recording must be a YouTube video.")
            download_url = candidate["url"]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    suffix = Path(video.filename or "source.mp4").suffix.lower() if video else ""
    if video and suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise HTTPException(status_code=422, detail="Upload an MP4, MOV, MKV, WEBM, or M4V video.")
    # Avoid an unbounded queue on the small processing server.
    with LOCK:
        if any(j["status"] in {"queued", "working", "uploading"} for j in JOBS.values()):
            raise HTTPException(status_code=409, detail="A project is already processing. Wait for it to finish.")
        job_id = uuid.uuid4().hex[:14]
        metadata = {"url": source["url"] if source else "", "title": "Video project"}
        JOBS[job_id] = {"job_id": job_id, "status": "uploading" if video else "queued", "progress": 0,
                        "message": "Preparing source...", "episode": metadata, "manifest": None}
    folder = DATA / job_id
    video_path = None
    try:
        folder.mkdir()
        if video:
            video_path = folder / ("source" + suffix)
            count = 0
            with video_path.open("wb") as destination:
                while block := await video.read(8 * 1024 * 1024):
                    count += len(block)
                    if count > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail=f"Maximum video size is {MAX_UPLOAD_BYTES / 1024**3:g} GB")
                    destination.write(block)
            if not count:
                raise HTTPException(status_code=422, detail="The uploaded video is empty.")
            metadata["title"] = Path(video.filename or "Video project").stem[:200]
        with LOCK:
            JOBS[job_id]["status"] = "queued"
        EXECUTOR.submit(_run_job, job_id, video_path, folder / "clips", metadata["url"], metadata["title"],
                        whisper, mode, clip_minutes, style, download_url, api_key)
    except Exception:
        if video_path:
            video_path.unlink(missing_ok=True)
        with LOCK:
            JOBS.pop(job_id, None)
        raise
    finally:
        if video:
            await video.close()
    return {"job_id": job_id, "status": "queued"}


@APP.get("/api/jobs")
def list_jobs():
    with LOCK:
        completed = [job for job in JOBS.values() if job["status"] == "complete"]
        completed.sort(key=lambda x: x["job_id"], reverse=True)
        return [{"job_id": job["job_id"], "episode": job["episode"],
                 "clip_count": job["manifest"].get("clip_count", 0)} for job in completed]


@APP.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return dict(job)


def job_output_path(job_id: str, requested: str):
    if not re.fullmatch(r"[0-9a-f]{14}", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    with LOCK:
        job = JOBS.get(job_id)
    if not job or job["status"] != "complete":
        raise HTTPException(status_code=404, detail="The clips are not ready")
    directory = (DATA / job_id / "clips").resolve()
    path = (directory / requested).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return path


@APP.get("/api/jobs/{job_id}/files/{requested:path}")
def get_file(job_id: str, requested: str):
    if requested.endswith(".mp4"):
        mimetype = "video/mp4"
    elif requested.endswith(".jpg"):
        mimetype = "image/jpeg"
    elif requested.endswith(".txt"):
        mimetype = "text/plain"
    else:
        mimetype = "application/octet-stream"
    return FileResponse(job_output_path(job_id, requested), media_type=mimetype)


@APP.get("/api/jobs/{job_id}/download")
def download_bundle(job_id: str):
    job_output_path(job_id, "manifest.json")
    target = DATA / job_id / "all_clips.zip"
    if not target.exists():
        directory = DATA / job_id / "clips"
        with zipfile.ZipFile(target, "w", allowZip64=True) as zf:
            for file in sorted(directory.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(directory), compress_type=zipfile.ZIP_STORED if file.suffix == ".mp4" else zipfile.ZIP_DEFLATED)
    return FileResponse(target, filename="podcast_clips.zip", media_type="application/zip")


if __name__ == "__main__":
    uvicorn.run(APP, host="0.0.0.0" if os.getenv("PUBLIC_DEPLOY") == "1" else "127.0.0.1", port=int(os.getenv("PORT", "8765")))
