"""Speech-to-text for videos without subtitles: yt-dlp downloads the audio, faster-whisper
transcribes it locally (free, no API). Runs on CPU; a 30-minute video takes roughly 10-25 minutes
with the default "turbo" (large-v3-turbo) model, so it runs as its own background job, one video
at a time.
"""
import logging
import tempfile
from pathlib import Path

from ..db import DB

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
_models: dict[str, object] = {}


def _model(size: str):
    # CPU by default: device="auto" picks an NVIDIA GPU when one exists, then fails if the
    # CUDA/cuDNN libraries aren't installed, which is the usual case on a home PC.
    if size not in _models:
        from faster_whisper import WhisperModel
        log.info("loading Whisper model %r (first time downloads it, ~1.6GB for turbo)", size)
        _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _models[size]


def download_audio(video_id: str, folder: Path, proxy: str | None = None,
                   cookies_from_browser: str | None = None, cookies_file: str | None = None) -> Path:
    """If YouTube answers "Sign in to confirm you're not a bot", let yt-dlp use your browser's
    YouTube login: cookies_from_browser="firefox" (or "edge"/"chrome"), or an exported cookies.txt."""
    import yt_dlp
    opts = {"format": "bestaudio/best", "outtmpl": str(folder / "%(id)s.%(ext)s"),
            "quiet": True, "no_warnings": True, "noprogress": True}
    if proxy:
        opts["proxy"] = proxy
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        return Path(ydl.prepare_filename(info))


# Whisper reads only the last ~224 tokens of a prompt (Chinese is ~1-2 tokens per character), so
# keep this short; the ticker list goes last and is capped.
BASE_PROMPT = "美股和宏观的普通话讲解，夹杂英文，代码保持英文，数字用阿拉伯数字：SPY 580 支撑，看 CPI 和 Fed rate cut。"
DEFAULT_TICKERS = ["SPY", "QQQ", "SPX", "NDX", "ES", "NQ", "IWM", "TQQQ", "SQQQ", "NVDA", "TSLA",
                   "AAPL", "MSFT", "META", "AMZN", "GOOGL", "AMD", "TLT", "VIX", "DXY"]


def build_prompt(extra_tickers: list[str] | None = None) -> str:
    """Whisper keeps English terms in English more reliably when the prompt is itself mixed
    Chinese/English and lists the words to expect. Whisper only reads ~224 tokens of prompt."""
    tickers = list(dict.fromkeys([*(extra_tickers or []), *DEFAULT_TICKERS]))[:25]
    return BASE_PROMPT + "代码：" + " ".join(tickers)


def recent_tickers(db: DB, limit: int = 25) -> list[str]:
    """Tickers the tracked authors talked about recently: the words most worth priming."""
    rows = db.query("SELECT ticker, COUNT(*) n FROM levels WHERE published_at >= datetime('now', '-60 days') "
                    "GROUP BY ticker ORDER BY n DESC LIMIT ?", (limit,))
    return [r["ticker"] for r in rows]


def transcribe_file(path: Path, model_size: str = "turbo", language: str | None = None,
                    prompt: str | None = None) -> str:
    segments, info = _model(model_size).transcribe(
        str(path), language=language, vad_filter=True, beam_size=5,
        # Nudges Chinese output to Simplified characters and keeps numbers as digits.
        initial_prompt=prompt or build_prompt(),
    )
    log.info("detected language %s (%.0f%%), %.0f min of audio",
             info.language, info.language_probability * 100, info.duration / 60)
    return "".join(seg.text for seg in segments).strip()


def transcribe_pending(db: DB, model_size: str = "turbo", language: str | None = None,
                       proxy: str | None = None, cookies_from_browser: str | None = None,
                       cookies_file: str | None = None) -> int:
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
                audio = download_audio(video_id, Path(tmp), proxy, cookies_from_browser, cookies_file)
                text = transcribe_file(audio, model_size, language, build_prompt(recent_tickers(db)))
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
