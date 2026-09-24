from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_digest.config import Source
from market_digest.sources import transcribe, youtube

SRC = [Source(kind="youtube", name="A", channel_id="UC1")]


def _new_video(monkeypatch, db, video_id="vid00000001"):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(youtube, "_from_feed", lambda cid: [{"id": video_id, "title": "市场概述", "published": now}])


def test_video_with_subtitles_goes_straight_to_extraction(db, monkeypatch):
    _new_video(monkeypatch, db)
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid: "SPY 580 是支撑")
    assert youtube.poll(db, SRC) == 1
    assert db.pending_items()[0]["content"] == "SPY 580 是支撑"


def test_disabled_subtitles_are_queued_for_transcription(db, monkeypatch):
    _new_video(monkeypatch, db)

    def disabled(vid):
        raise youtube.SubtitlesDisabled(vid)

    monkeypatch.setattr(youtube, "fetch_transcript", disabled)
    youtube.poll(db, SRC)
    assert db.query("SELECT status FROM items")[0]["status"] == "transcribe"


def test_missing_subtitles_wait_before_transcribing(db, monkeypatch):
    _new_video(monkeypatch, db)
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid: None)
    youtube.poll(db, SRC)
    assert db.query("SELECT status FROM items")[0]["status"] == "waiting"  # auto-captions may still come

    long_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db.execute("UPDATE items SET fetched_at=?", (long_ago,))
    youtube.poll(db, SRC)
    assert db.query("SELECT status FROM items")[0]["status"] == "transcribe"


def test_transcription_fills_content(db, monkeypatch, tmp_path):
    _new_video(monkeypatch, db)
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid: None)
    youtube.poll(db, SRC, transcribe=True)
    db.mark_item("youtube:vid00000001", "transcribe")
    monkeypatch.setattr(transcribe, "download_audio", lambda vid, folder, proxy=None: Path(folder) / "a.m4a")
    monkeypatch.setattr(transcribe, "transcribe_file", lambda path, size, lang: "纳指 20000 是阻力")

    assert transcribe.transcribe_pending(db) == 1
    item = db.pending_items()[0]
    assert "纳指 20000 是阻力" in item["content"] and "语音识别" in item["content"]


def test_transcription_gives_up_after_repeated_failures(db, monkeypatch):
    _new_video(monkeypatch, db)
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid: None)
    youtube.poll(db, SRC)
    db.mark_item("youtube:vid00000001", "transcribe")

    def boom(*a, **k):
        raise RuntimeError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(transcribe, "download_audio", boom)
    for _ in range(transcribe.MAX_ATTEMPTS):
        transcribe.transcribe_pending(db)
    assert db.query("SELECT status FROM items")[0]["status"] == "error"
