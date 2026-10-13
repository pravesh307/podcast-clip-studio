import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app
import engine
import sources

client = TestClient(app.APP)
YT = 'https://www.youtube.com/watch?v=abcdefghijk'
SP = 'https://open.spotify.com/episode/0ZLhKjfvOYlosTelLqBaqZ'


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(app, 'DATA', tmp_path)
    monkeypatch.setattr(app, 'JOBS', {})
    monkeypatch.delenv('APP_PASSWORD', raising=False)
    monkeypatch.delenv('PUBLIC_DEPLOY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)


@pytest.mark.parametrize('url', [YT, 'https://youtu.be/abcdefghijk?t=90', 'https://m.youtube.com/watch?v=abcdefghijk&list=test', 'https://www.youtube.com/live/abcdefghijk'])
def test_youtube_canonicalization(url):
    assert sources.parse_source(url)['url'] == YT


@pytest.mark.parametrize('url', ['https://youtube.com.attacker.test/watch?v=abcdefghijk', 'https://127.0.0.1/video', 'file:///etc/passwd', 'https://youtube.com:8443/watch?v=abcdefghijk', 'https://user:password@youtube.com/watch?v=abcdefghijk', 'https://youtube.com/playlist?list=xyz'])
def test_disallowed_source(url):
    with pytest.raises(ValueError):
        sources.parse_source(url)


def submit(monkeypatch, **changes):
    calls = []
    monkeypatch.setattr(app.EXECUTOR, 'submit', lambda *args: calls.append(args))
    data = dict(source_url=YT, mode='draft', clip_minutes=7, rights_confirmed='true')
    data.update(changes)
    return client.post('/api/jobs', data=data), calls


def test_link_only_job_no_upload(monkeypatch):
    response, calls = submit(monkeypatch)
    assert response.status_code == 202
    assert calls[0][2] is None
    assert calls[0][8] == 7
    assert calls[0][10] == YT


@pytest.mark.parametrize('length', [2, 16, 0, 'not-a-number'])
def test_api_rejects_invalid_length(monkeypatch, length):
    response, calls = submit(monkeypatch, clip_minutes=length)
    assert response.status_code == 422
    assert not calls


def test_spotify_requires_confirmed_youtube_version(monkeypatch):
    response, calls = submit(monkeypatch, source_url=SP)
    assert response.status_code == 422
    assert not calls
    response, calls = submit(monkeypatch, source_url=SP, youtube_url=YT)
    assert response.status_code == 202
    assert calls[0][10] == YT


def test_smart_ai_key_not_stored_in_job(monkeypatch):
    response, calls = submit(monkeypatch, mode='smart', api_key='test-only-key')
    assert response.status_code == 202
    assert 'test-only-key' not in json.dumps(app.JOBS)
    assert calls[0][-1] == 'test-only-key'


def test_smart_requires_key(monkeypatch):
    response, calls = submit(monkeypatch, mode='smart')
    assert response.status_code == 422
    assert not calls


def test_single_job_queue(monkeypatch):
    response, _ = submit(monkeypatch)
    assert response.status_code == 202
    response, _ = submit(monkeypatch)
    assert response.status_code == 409


@pytest.mark.parametrize('minutes', [3, 4, 7, 10, 15])
def test_draft_durations(minutes):
    segments = [engine.Segment(i, i+4, 'A complete story with enough words for a useful conversation about history and archaeology.') for i in range(0, 3000, 5)]
    clips = engine.fallback_highlights(segments, 3000, minutes)
    lower, upper, _ = engine.duration_bounds(minutes)
    assert clips
    assert all(lower <= clip.end-clip.start <= upper for clip in clips)
    assert all(a.end <= b.start for a,b in zip(clips, clips[1:]))


def test_ai_long_clips_and_metadata(monkeypatch):
    import openai
    prompts = []
    def create(**kwargs):
        prompts.append(kwargs)
        body = {'clips': [{'start': 0, 'end': 900, 'title': 'A funny historical misunderstanding', 'score': 85,
                           'thumbnail_text': 'THAT WAS THE PLAN?', 'thumbnail_time': 450,
                           'title_variants': ['A', 'B', 'C']},
                          {'start': 0, 'end': 99999, 'title': 'Invalid', 'score': 99}]}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))])
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    segments = [engine.Segment(i, i+5, 'A historical story with an unexpected funny twist that has plenty of conversational context.') for i in range(0, 2400, 5)]
    clips = engine.smart_highlights(segments, 2400, lambda *a: None, 'test', 15, 'funny', 'dummy')
    assert len(clips) == 1
    assert clips[0].end-clips[0].start == 900
    assert clips[0].title_variants == ['A','B','C']
    assert clips[0].thumbnail_text == 'THAT WAS THE PLAN?'
    assert '900 seconds' in prompts[0]['messages'][0]['content']


def test_download_to_render_pipeline(monkeypatch, tmp_path):
    video = tmp_path/'source.mp4'
    video.write_bytes(b'test')
    app.JOBS['abc'] = {'status': 'queued', 'episode': {}}
    monkeypatch.setattr(app, 'download_youtube', lambda *args: (video, 'Imported title'))
    captured = {}
    def render(*args, **kwargs):
        captured.update(kwargs)
        return {'clip_count': 1, 'clips': []}
    monkeypatch.setattr(app, 'render_job', render)
    app._run_job('abc', None, tmp_path/'clips', YT, '', 'base', 'smart', 15, 'funny', YT, 'secret')
    assert app.JOBS['abc']['status'] == 'complete'
    assert app.JOBS['abc']['episode']['title'] == 'Imported title'
    assert captured['clip_minutes'] == 15
    assert not video.exists()


def test_provider_failure_redacts_key(monkeypatch, tmp_path):
    video = tmp_path/'source.mp4'
    video.write_bytes(b'test')
    app.JOBS['abc'] = {'status': 'queued', 'episode': {}}
    def fail(*a, **kw):
        raise RuntimeError('Invalid API key secret-test')
    monkeypatch.setattr(app, 'render_job', fail)
    app._run_job('abc', video, tmp_path/'clips', YT, '', 'base', 'smart', 4, 'funny', YT, 'secret-test')
    assert app.JOBS['abc']['status'] == 'failed'
    assert 'secret-test' not in app.JOBS['abc']['message']
