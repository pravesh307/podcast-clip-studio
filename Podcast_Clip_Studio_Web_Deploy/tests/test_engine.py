import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from engine import Segment, Highlight, fallback_highlights, _overlap_fraction, _window_ranges, probe_video, make_thumbnail, render_video


def test_window_ranges_full_coverage():
    windows = list(_window_ranges(5220))
    assert windows[0] == (0, 900)
    assert windows[-1][1] == 5220
    assert all(b > a for a, b in windows)
    assert all(x[1] >= y[0] for x, y in zip(windows, windows[1:]))


def test_draft_cuts_respect_duration_and_are_not_infinite():
    # One transcript sentence every 5 seconds throughout a 12-minute recording.
    segments = [Segment(float(i), float(i+4), "This is a longer and useful standalone conversation about archaeology and historical evidence.")
                for i in range(0, 720, 5)]
    clips = fallback_highlights(segments, 720.0)
    assert 2 <= len(clips) <= 4
    assert all(180 <= x.end - x.start <= 240 for x in clips)
    assert all(a.end <= b.start for a,b in zip(clips, clips[1:]))


def test_overlap_fraction():
    a = Highlight(120, 340, "First")
    b = Highlight(200, 420, "Second")
    c = Highlight(500, 720, "Third")
    assert round(_overlap_fraction(a, b), 2) == 0.64
    assert _overlap_fraction(a, c) == 0


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    target = tmp_path_factory.mktemp("video") / "source.mp4"
    command = ["ffmpeg","-hide_banner","-loglevel","error","-y","-f","lavfi","-i","color=c=0x43305e:s=320x180:r=2",
               "-f","lavfi","-i","sine=frequency=440:sample_rate=16000", "-t","181",
               "-c:v","libx264","-preset","ultrafast","-crf","40","-pix_fmt","yuv420p","-c:a","aac",str(target)]
    subprocess.run(command, capture_output=True, check=True)
    return target


def test_probe_and_export_1080p(sample_video, tmp_path):
    info = probe_video(sample_video)
    assert info["duration"] >= 180
    assert (info["width"],info["height"]) == (320,180)
    dest = tmp_path / "clip.mp4"
    render_video(sample_video, dest, 0, 2)
    info_out = probe_short(dest)
    assert info_out["width"] == 1920 and info_out["height"] == 1080
    assert 1.8 < info_out["duration"] < 2.4
    assert dest.stat().st_size > 1000


def probe_short(video: Path):
    output = subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration:stream=width,height", "-of","json",str(video)],capture_output=True,text=True,check=True)
    info = json.loads(output.stdout)
    stream = next(s for s in info["streams"] if "width" in s)
    return {"width":stream["width"],"height":stream["height"],"duration":float(info["format"]["duration"])}


def test_thumbnail_size(sample_video, tmp_path):
    dest=tmp_path / "thumbnail.jpg"
    make_thumbnail(sample_video, dest, 2, "The Conversation About History", "Podcast Test Episode")
    with Image.open(dest) as im:
        assert im.size == (1280,720)
        assert im.format == "JPEG"


def test_complete_project_manifest(sample_video, tmp_path, monkeypatch):
    import engine
    segments = [Segment(float(i), float(i+4),
                        "The guest explains how explorers interpret historical discoveries and archaeological data.")
                for i in range(0,181,5)]
    monkeypatch.setattr(engine, "transcribe", lambda video, model, report: segments)
    monkeypatch.setattr(engine, "render_video", lambda video, dest, start, end: dest.write_bytes(b"demonstration mp4"))
    monkeypatch.setattr(engine, "make_thumbnail", lambda video, dest, timestamp, title, episode: Image.new("RGB", (1280,720)).save(dest, "JPEG"))
    reports = []
    manifest = engine.render_job(sample_video, tmp_path / "output",
                                "https://open.spotify.com/episode/0ZLhKjfvOYlosTelLqBaqZ",
                                "Test Episode", "base", "draft", "not-used",
                                lambda msg, progress: reports.append((msg, progress)))
    assert manifest["clip_count"] >= 1
    assert (tmp_path / "output" / "manifest.json").exists()
    assert (tmp_path / "output" / "clips.csv").exists()
    for clip in manifest["clips"]:
        assert (tmp_path / "output" / clip["video"]).exists()
        assert (tmp_path / "output" / clip["thumbnail"]).exists()
        assert clip["duration"] >= 180
    assert reports[-1][1] == 100
