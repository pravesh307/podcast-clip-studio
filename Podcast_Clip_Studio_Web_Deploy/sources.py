"""Public YouTube imports and Spotify-to-YouTube discovery; no protected streams."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import re
import shutil

import requests


def parse_source(url: str) -> dict:
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
    except ValueError:
        raise ValueError("Use an HTTPS YouTube video or Spotify episode link.") from None
    host = (parsed.hostname or "").lower()
    if host == "open.spotify.com":
        match = re.fullmatch(r"/(?:intl-[a-z]+/)?episode/([A-Za-z0-9]{10,32})/?", parsed.path)
        if match:
            return {"platform": "spotify", "id": match[1], "url": f"https://open.spotify.com/episode/{match[1]}"}
    ident = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            ident = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([\w-]{11})/?", parsed.path)
            ident = match[1] if match else ""
    elif host == "youtu.be":
        ident = parsed.path.strip("/")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
        return {"platform": "youtube", "id": ident, "url": f"https://www.youtube.com/watch?v={ident}"}
    raise ValueError("Paste a single YouTube video or Spotify episode link, not a playlist or channel.")


def source_info(url: str) -> dict:
    info = parse_source(url)
    endpoint = "https://www.youtube.com/oembed" if info["platform"] == "youtube" else "https://open.spotify.com/oembed"
    info.update(title="YouTube video" if info["platform"] == "youtube" else "Spotify episode", thumbnail=None)
    try:
        response = requests.get(endpoint, params={"url": info["url"], "format": "json"}, timeout=12)
        response.raise_for_status()
        meta = response.json()
        info.update(title=str(meta.get("title") or info["title"])[:200], thumbnail=meta.get("thumbnail_url"), author=meta.get("author_name", ""))
    except (requests.RequestException, ValueError):
        info["note"] = "Link recognised; the provider could not return its title right now."
    return info


def youtube_options() -> dict:
    return {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20,
            "retries": 2, "fragment_retries": 2, "js_runtimes": {"node": {}}, "cachedir": False}


def find_youtube_versions(title: str) -> list[dict]:
    from yt_dlp import YoutubeDL
    try:
        with YoutubeDL({**youtube_options(), "extract_flat": "in_playlist", "skip_download": True}) as ydl:
            result = ydl.extract_info(f"ytsearch5:{title[:200]} full podcast", download=False)
        candidates = []
        for entry in (result or {}).get("entries", []):
            if not entry or not re.fullmatch(r"[A-Za-z0-9_-]{11}", entry.get("id", "")):
                continue
            candidates.append({"url": f"https://www.youtube.com/watch?v={entry['id']}",
                               "title": str(entry.get("title") or "YouTube video")[:200],
                               "channel": str(entry.get("channel") or entry.get("uploader") or ""),
                               "duration": entry.get("duration")})
        return candidates
    except Exception:
        raise RuntimeError("YouTube search is unavailable from this server. Paste the episode's YouTube link instead.") from None


def download_youtube(url: str, folder: Path, report, max_bytes: int) -> tuple[Path, str]:
    from yt_dlp import YoutubeDL
    source = parse_source(url)
    if source["platform"] != "youtube":
        raise ValueError("Select the matching YouTube version for this Spotify episode.")
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < 512 * 1024**2:
        raise RuntimeError("The server is low on disk space. Remove old projects or add storage.")

    def progress(status):
        downloaded = status.get("downloaded_bytes", 0)
        if downloaded > max_bytes or sum(p.stat().st_size for p in folder.glob("source*")) > max_bytes:
            raise RuntimeError("This recording exceeds the server's download size limit.")
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        fraction = min(1, downloaded / total) if total else 0
        report(f"Downloading source video{f': {fraction:.0%}' if total else '...'}", round(2 + 10 * fraction))

    def eligible(info, *, incomplete=False):
        if info.get("is_live") or info.get("live_status") == "is_upcoming":
            return "Choose a finished recording, not an active or upcoming livestream."
        if (info.get("duration") or 0) > 6 * 3600:
            return "Choose a recording shorter than six hours."
        if info.get("has_drm"):
            return "Protected recordings cannot be imported."

    report("Connecting to YouTube...", 2)
    options = {**youtube_options(), "format": "bv*[height<=1080]+ba/b[height<=1080]",
               "outtmpl": str(folder / "source.%(ext)s"), "merge_output_format": "mp4",
               "max_filesize": max_bytes, "progress_hooks": [progress], "match_filter": eligible,
               "concurrent_fragment_downloads": 1, "overwrites": True}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(source["url"], download=True)
        paths = [p for p in folder.glob("source.*") if p.suffix in {".mp4", ".mkv", ".webm", ".mov"}]
        if not paths:
            raise RuntimeError("No playable video was returned; it may exceed the size limit or be unavailable.")
        video = max(paths, key=lambda p: p.stat().st_size)
        if video.stat().st_size > max_bytes:
            raise RuntimeError("This recording exceeds the server's download size limit.")
        return video, str((info or {}).get("title") or "YouTube video")[:200]
    except Exception as exc:
        for path in folder.glob("source*"):
            if path.is_file():
                path.unlink(missing_ok=True)
        # Do not expose signed media URLs or provider internals in job records.
        if isinstance(exc, RuntimeError) and not type(exc).__module__.startswith("yt_dlp"):
            raise
        raise RuntimeError("YouTube could not provide this recording to the server. It may require sign-in, be restricted, or block cloud downloads. Try another accessible video or the optional upload.") from None
