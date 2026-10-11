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
from pydantic import BaseModel

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
                permitted = hmac.compare_digest(user, "studio") and hmac.compare_digest(supplied, password)
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
    episode_id = parse_episode_id(url)
    canonical = f"https://open.spotify.com/episode/{episode_id}"
    result = {"id": episode_id, "url": canonical, "title": "Podcast Episode", "thumbnail": None}
    try:
        response = requests.get("https://open.spotify.com/oembed", params={"url": canonical}, timeout=9)
        response.raise_for_status()
        meta = response.json()
        result["title"] = str(meta.get("title") or "Podcast Episode")[:200]
        result["thumbnail"] = meta.get("thumbnail_url")
    except (requests.RequestException, ValueError):
        result["note"] = "Episode ID recognised, but Spotify metadata could not be loaded right now."
    return result


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


def _run_job(job_id: str, video: Path, output: Path, url: str, title: str, whisper_model: str, selection_mode: str):
    with LOCK:
        JOBS[job_id]["status"] = "working"
    try:
        manifest = render_job(video, output, url, title, whisper_model, selection_mode,
                              os.getenv("OPENAI_CLIP_MODEL", "gpt-4.1-mini"),
                              lambda message, progress: _update_job(job_id, message, progress))
        with LOCK:
            JOBS[job_id].update(status="complete", manifest=manifest, progress=100, message="All clips are ready")
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(status="failed", message=f"{type(exc).__name__}: {exc}")


@APP.post("/api/jobs", status_code=202)
async def create_job(spotify_url: str = Form(...), video: UploadFile = File(...),
                     mode: str = Form("smart"), whisper: str = Form("small")):
    try:
        metadata = episode_info(spotify_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if mode not in {"smart", "draft"}:
        raise HTTPException(status_code=422, detail="Unknown clipping mode")
    if whisper not in {"base", "small", "medium"}:
        raise HTTPException(status_code=422, detail="Unknown transcription size")
    if mode == "smart" and not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=422, detail="Smart mode requires OPENAI_API_KEY. Set it in your terminal, or choose Draft mode.")
    suffix = Path(video.filename or "podcast.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise HTTPException(status_code=422, detail="Upload an MP4, MOV, MKV, WEBM, or M4V video file")
    job_id = uuid.uuid4().hex[:14]
    folder = DATA / job_id
    folder.mkdir()
    video_path = folder / ("source" + suffix)
    count = 0
    try:
        with video_path.open("wb") as destination:
            while block := await video.read(8 * 1024 * 1024):
                count += len(block)
                if count > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Maximum video size is 12 GB")
                destination.write(block)
    except Exception:
        video_path.unlink(missing_ok=True)
        raise
    finally:
        await video.close()
    with LOCK:
        JOBS[job_id] = {"job_id": job_id, "status": "queued", "progress": 0, "message": "Waiting for processing slot...",
                        "episode": metadata, "manifest": None}
    EXECUTOR.submit(_run_job, job_id, video_path, folder / "clips", metadata["url"], metadata["title"], whisper, mode)
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
