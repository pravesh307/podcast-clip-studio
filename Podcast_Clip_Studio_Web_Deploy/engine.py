"""Podcast Clip Studio: transcription, highlights, 1080p rendering and thumbnails.

This engine works with a local, authorised source video. It does NOT access
Spotify audio/video streams, bypass DRM, or download copyrighted content.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont, ImageOps

Progress = Callable[[str, int], None]


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Highlight:
    start: float
    end: float
    title: str
    reason: str = ""
    score: float = 0.0
    source: str = "draft"


def verify_ffmpeg() -> None:
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"{executable} is missing. Install FFmpeg first (e.g. brew install ffmpeg).")


def probe_video(path: Path) -> dict:
    verify_ffmpeg()
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video_streams = [x for x in streams if x.get("codec_type") == "video"]
    if not video_streams:
        raise ValueError("This file does not have a video track. Upload an MP4, MOV, or other video.")
    dur = float(payload.get("format", {}).get("duration") or 0)
    if dur < 180:
        raise ValueError("The source video is shorter than three minutes.")
    s = video_streams[0]
    return {"duration": dur, "width": s.get("width"), "height": s.get("height")}


def transcribe(video: Path, model_name: str, report: Progress) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed. Run: pip install -r requirements.txt") from exc
    report(f"Loading Whisper '{model_name}' transcription model (first use may download it)...", 8)
    model = WhisperModel(model_name, device="auto", compute_type="int8")
    report("Transcribing the podcast. This may take a while for multi-hour videos...", 12)
    iterable, _info = model.transcribe(str(video), beam_size=3, vad_filter=True)
    segments = [Segment(float(x.start), float(x.end), x.text.strip()) for x in iterable if x.text.strip()]
    if not segments:
        raise RuntimeError("No spoken dialogue could be transcribed from the video.")
    report(f"Transcript complete: {len(segments)} timestamped passages.", 38)
    return segments


def _choose_boundary(segments: list[Segment], desired: float, lower: float, upper: float) -> float | None:
    """Prefer a speech-end boundary near the desired time."""
    options = []
    for seg in segments:
        if lower <= seg.end <= upper:
            punctuation = 10 if seg.text.rstrip().endswith((".", "?", "!")) else 0
            # Reward pauses that follow a passage, avoiding sentence midpoints.
            options.append((abs(seg.end - desired) - punctuation, seg.end))
    return min(options)[1] if options else None


def _extract_text(segments: list[Segment], start: float, end: float) -> str:
    return " ".join(seg.text for seg in segments if seg.end > start and seg.start < end)


def _draft_title(text: str, index: int) -> str:
    words = re.findall(r"\b[a-zA-Z][\w'-]{3,}\b", text)
    ignored = {"about", "because", "there", "their", "would", "could", "should", "really", "think", "thing", "things", "people", "going", "something", "actually", "right", "that's", "you're", "which", "where", "after", "before", "these", "those", "maybe", "don't", "know", "just", "like", "have", "with", "that", "from", "your", "this", "they", "what", "when", "were", "been", "then", "more", "very", "much", "some", "into", "also", "even", "does", "well", "yeah", "okay", "said", "mean"}
    counts: dict[str, int] = {}
    for word in words:
        w = word.lower().strip("'-")
        if w not in ignored:
            counts[w] = counts.get(w, 0) + 1
    topics = sorted(counts, key=lambda w: (-counts[w], w))[:2]
    if topics:
        return f"A Conversation About {' & '.join(x.title() for x in topics)}"
    return f"Podcast Highlight {index:02d}"


def fallback_highlights(segments: list[Segment], total_duration: float) -> list[Highlight]:
    """Local baseline, NOT context-aware semantic intelligence.

    Makes adjacent 3-4 minute candidate drafts, based on speech pauses and
    sentences. For genuinely intelligent choice use OpenAI mode below.
    """
    highlights: list[Highlight] = []
    cursor = max(0.0, segments[0].start)
    while cursor + 180 <= total_duration:
        boundary = _choose_boundary(segments, cursor + 210, cursor + 180, min(cursor + 240, total_duration))
        if boundary is None:
            boundary = min(cursor + 210, total_duration)
        if boundary - cursor < 180:
            break
        text = _extract_text(segments, cursor, boundary)
        if len(text.split()) >= 110:
            highlights.append(Highlight(cursor, boundary, _draft_title(text, len(highlights) + 1), "Automatic rough-cut draft; review context before publishing", 0.0, "draft"))
        cursor = boundary
    return highlights


def _window_ranges(duration: float, window: int = 900, overlap: int = 240):
    cursor = 0
    while cursor < duration:
        yield cursor, min(duration, cursor + window)
        if cursor + window >= duration:
            break
        cursor += window - overlap


def _nearest_start(t: float, segments: list[Segment]) -> float:
    boundaries = [s.start for s in segments if abs(s.start - t) <= 8]
    return min(boundaries, key=lambda x: abs(x-t)) if boundaries else t


def _nearest_end(t: float, segments: list[Segment]) -> float:
    boundaries = [s.end for s in segments if abs(s.end - t) <= 8]
    return min(boundaries, key=lambda x: abs(x-t)) if boundaries else t


def _overlap_fraction(a: Highlight, b: Highlight) -> float:
    overlap = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return overlap / max(1.0, min(a.end - a.start, b.end - b.start))


def smart_highlights(segments: list[Segment], total_duration: float, report: Progress, model: str) -> list[Highlight]:
    """Use the user's own OpenAI API key to assess all 15-minute windows.

    Prompts are recommendations; strict validation happens locally.
    """
    from openai import OpenAI
    client = OpenAI(timeout=150, max_retries=2)
    candidates: list[Highlight] = []
    windows = list(_window_ranges(total_duration))
    for index, (start, stop) in enumerate(windows):
        selected = [s for s in segments if s.end >= start and s.start <= stop]
        if len(selected) < 3:
            continue
        transcript = "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in selected)
        system = (
            "You are a meticulous senior podcast video producer. Return strictly a JSON object with key 'clips' "
            "containing a list of objects with keys: start (seconds float), end (seconds float), "
            "title (specific, truthful, catchy but never sensationally misrepresenting the speaker), "
            "score (0-100), reason (one sentence). "
            "Review EVERY worthwhile standalone 180-240-second passage, not just the single best moment. "
            "Reject boring, repetitive, confusing, contextless sections. Each excerpt must start at a natural "
            "speech boundary, make sense to a new viewer, and end after a meaningful point. "
            "No fabricated quotes. Do not pick clips requiring outside context. "
            "Use exact seconds within the provided window. Include only very high-confidence clips. "
            "Return {\"clips\": []} if there are none."
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": f"Window {start:.1f} to {stop:.1f} seconds.\nTRANSCRIPT:\n{transcript}"}],
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(resp.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            parsed = {}
        for item in parsed.get("clips", []):
            if not isinstance(item, dict):
                continue
            try:
                a = float(item["start"])
                b = float(item["end"])
                title = str(item["title"]).strip()[:105]
                score = float(item.get("score", 0))
                reason = str(item.get("reason", ""))[:300]
            except (TypeError, ValueError, KeyError):
                continue
            a = _nearest_start(a, segments)
            b = _nearest_end(b, segments)
            if not (start - 8 <= a < b <= stop + 8 and 180 <= b-a <= 240 and 0 <= a < total_duration and b <= total_duration + 0.1):
                continue
            if not (title and len(_extract_text(segments, a, b).split()) >= 100):
                continue
            if score < 67:
                continue
            candidates.append(Highlight(round(a, 2), round(b, 2), title, reason, score, "ai"))
        report(f"AI analysed {index+1}/{len(windows)} transcript windows...", 40 + round(25 * (index + 1) / len(windows)))
    # First pick strongest candidate, then avoid near-duplicates and overlapping conversations.
    chosen: list[Highlight] = []
    for candidate in sorted(candidates, key=lambda item: -item.score):
        if all(_overlap_fraction(candidate, x) < 0.30 for x in chosen):
            chosen.append(candidate)
    chosen.sort(key=lambda x: x.start)
    return chosen


def _safe_stem(text: str, max_len: int = 60) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", text).strip("_")
    return (stem[:max_len] or "highlight")


def render_video(video: Path, dest: Path, a: float, b: float) -> None:
    duration = b-a
    vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.3f}", "-i", str(video),
               "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Video rendering failed: {result.stderr[-1500:]}")


def _font(size: int):
    paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_thumbnail(video: Path, dest: Path, timestamp: float, title: str, episode_title: str) -> None:
    # Extract the actual authorised video frame, not AI-generated substitute footage.
    still = dest.with_suffix(".frame.jpg")
    process = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(timestamp), "-i", str(video),
                              "-frames:v", "1", "-q:v", "2", str(still)], capture_output=True, text=True)
    if process.returncode != 0 or not still.exists():
        raise RuntimeError(f"Thumbnail frame extraction failed: {process.stderr[-500:]}")
    try:
        with Image.open(still) as opened:
            background = ImageOps.fit(opened.convert("RGB"), (1280, 720), method=Image.Resampling.LANCZOS)
        mask = Image.new("RGBA", background.size, (0, 0, 0, 0))
        overlay = ImageDraw.Draw(mask)
        for x in range(980):
            alpha = round(228 * (1 - x / 1050))
            overlay.line([(x, 0), (x, 720)], fill=(5, 8, 18, max(alpha, 0)))
        background = Image.alpha_composite(background.convert("RGBA"), mask)
        d = ImageDraw.Draw(background)
        d.rounded_rectangle((62, 69, 240, 105), radius=15, fill=(100, 83, 247, 255))
        d.text((77, 78), "PODCAST CLIP", font=_font(18), fill="white")
        # Keep the title readable on all images, hard cap the lines and width.
        words = title.strip().upper().split()
        lines: list[str] = []
        line = ""
        font = _font(69)
        for word in words:
            new = f"{line} {word}".strip()
            if d.textbbox((0, 0), new, font=font)[2] <= 800 or not line:
                line = new
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        lines = lines[:4]
        y = 315 - len(lines) * 40
        for line in lines:
            # outline is subtle and keeps large text legible
            d.text((58, y), line, font=font, fill="white", stroke_width=2, stroke_fill=(0, 0, 0, 120))
            y += 86
        d.rounded_rectangle((61, 655, 130, 663), radius=3, fill=(132, 114, 255, 255))
        d.text((147, 641), episode_title[:75], font=_font(21), fill=(235, 237, 250, 255))
        background.convert("RGB").save(dest, "JPEG", quality=91, optimize=True)
    finally:
        still.unlink(missing_ok=True)


def render_job(video: Path, output_dir: Path, spotify_url: str, episode_title: str, whisper_model: str,
               selection_mode: str, ai_model: str, report: Progress) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    info = probe_video(video)
    report(f"Source verified: {info['duration']/60:.1f} min, {info['width']}×{info['height']} pixels", 5)
    segments = transcribe(video, whisper_model, report)
    (output_dir / "transcript.json").write_text(json.dumps([asdict(x) for x in segments], indent=2), encoding="utf8")
    if selection_mode == "smart":
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("AI mode needs OPENAI_API_KEY in your environment. Use Draft mode without a key.")
        report("Finding genuinely meaningful conversation highlights with AI...", 40)
        clips = smart_highlights(segments, info["duration"], report, ai_model)
    else:
        report("Finding transcript-based rough-cut drafts...", 55)
        clips = fallback_highlights(segments, info["duration"])
    if not clips:
        raise RuntimeError("No qualifying clips were detected. Try Draft mode or review the transcription.")
    clip_info = []
    for i, item in enumerate(clips, start=1):
        slug = _safe_stem(item.title)
        folder = output_dir / f"{i:02d}_{slug}"
        folder.mkdir(exist_ok=True)
        mp4 = folder / "video.mp4"
        jpg = folder / "thumbnail.jpg"
        report(f"Rendering clip {i}/{len(clips)}: {item.title}", 67 + round(31 * (i-1)/len(clips)))
        render_video(video, mp4, item.start, item.end)
        make_thumbnail(video, jpg, min(item.end-2, item.start+8), item.title, episode_title)
        (folder / "title.txt").write_text(item.title, encoding="utf8")
        metadata = asdict(item)
        metadata.update({"duration": round(item.end - item.start, 2), "video": f"{folder.name}/video.mp4", "thumbnail": f"{folder.name}/thumbnail.jpg"})
        (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf8")
        clip_info.append(metadata)
    manifest = {"episode": episode_title, "spotify_url": spotify_url, "source_video_info": info,
                "selection_mode": selection_mode, "clip_count": len(clip_info), "clips": clip_info}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    with (output_dir / "clips.csv").open("w", newline="", encoding="utf8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["start", "end", "duration", "title", "score", "reason", "video", "thumbnail"])
        writer.writeheader()
        for clip in clip_info:
            writer.writerow({k: clip.get(k, "") for k in writer.fieldnames})
    report(f"Finished! {len(clips)} clips created.", 100)
    return manifest
