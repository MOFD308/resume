"""Speech-to-text for videos without subtitles: yt-dlp downloads the audio, faster-whisper
transcribes it locally (free, no API). Runs on CPU; a 30-minute video takes roughly 5-15 minutes
with the default "small" model, so it runs as its own background job, one video at a time.
"""
import logging
import tempfile
from pathlib import Path

from ..db import DB

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
_models: dict[str, object] = {}


def _model(size: str):
    if size not in _models:
        from faster_whisper import WhisperModel
        log.info("loading Whisper model %r (first time downloads it)", size)
        _models[size] = WhisperModel(size, device="auto", compute_type="int8")
    return _models[size]


def download_audio(video_id: str, folder: Path, proxy: str | None = None) -> Path:
    import yt_dlp
    opts = {"format": "bestaudio/best", "outtmpl": str(folder / "%(id)s.%(ext)s"),
            "quiet": True, "no_warnings": True, "noprogress": True}
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        return Path(ydl.prepare_filename(info))


def transcribe_file(path: Path, model_size: str = "small", language: str | None = None) -> str:
    segments, info = _model(model_size).transcribe(
        str(path), language=language, vad_filter=True, beam_size=5,
        # Nudges Chinese output to Simplified characters and keeps numbers as digits.
        initial_prompt="以下是关于美股、期货和宏观经济的普通话讲解，使用简体中文，数字用阿拉伯数字，如 SPY 580、纳指 20000。",
    )
    log.info("detected language %s (%.0f%%), %.0f min of audio",
             info.language, info.language_probability * 100, info.duration / 60)
    return "".join(seg.text for seg in segments).strip()


def transcribe_pending(db: DB, model_size: str = "small", language: str | None = None,
                       proxy: str | None = None) -> int:
    """Transcribe queued videos one by one. Returns how many became ready for extraction."""
    done = 0
    for item in db.query("SELECT id, title FROM items WHERE kind='youtube' AND status='transcribe' "
                         "ORDER BY published_at DESC"):
        video_id = item["id"].split(":", 1)[1]
        attempts_key = f"transcribe:attempts:{video_id}"
        attempts = int(db.get_kv(attempts_key, "0")) + 1
        db.set_kv(attempts_key, str(attempts))
        log.info("transcribing %s (%s), attempt %d", video_id, item["title"], attempts)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                audio = download_audio(video_id, Path(tmp), proxy)
                text = transcribe_file(audio, model_size, language)
        except Exception as e:
            log.warning("transcription failed for %s: %s", video_id, " ".join(str(e).split())[:200])
            if attempts >= MAX_ATTEMPTS:
                db.mark_item(item["id"], "error", f"transcription failed: {e}"[:500])
            continue
        if not text:
            db.mark_item(item["id"], "error", "transcription produced no text")
            continue
        db.execute("UPDATE items SET content=?, status='pending' WHERE id=?",
                   ("[语音识别转写，数字可能有误]\n" + text, item["id"]))
        done += 1
    return done
