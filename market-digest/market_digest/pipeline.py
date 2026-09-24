"""Poll every source, then run Claude over whatever is new."""
import logging
import os
import threading

from . import newsletter
from .config import Config
from .db import DB
from .llm import LLM
from .sources import transcribe, website, x, youtube

log = logging.getLogger(__name__)

_process_lock = threading.Lock()


def poll_sources(cfg: Config, db: DB) -> None:
    for name, fn in (
        ("youtube", lambda: youtube.poll(db, cfg.by_kind("youtube"),
                                         transcribe=_transcribe_cfg(cfg).get("enabled", True),
                                         language=_transcribe_cfg(cfg).get("language"))),
        ("website", lambda: website.poll(db, cfg.by_kind("website"))),
        ("x", lambda: x.poll(db, cfg.by_kind("x"), cfg.get("email_inbox"))),
    ):
        try:
            n = fn()
            if n:
                log.info("%s: %d new item(s)", name, n)
        except Exception:
            log.exception("polling %s failed", name)


def _transcribe_cfg(cfg: Config) -> dict:
    return cfg.get("youtube_transcribe") or {}


def transcribe_videos(cfg: Config, db: DB, llm: LLM) -> None:
    """Background job: speech-to-text for videos without subtitles, then extract."""
    tc = _transcribe_cfg(cfg)
    if not tc.get("enabled", True):
        return
    try:
        n = transcribe.transcribe_pending(db, tc.get("model", "small"), tc.get("language"),
                                          os.environ.get("YOUTUBE_PROXY"),
                                          cookies_from_browser=tc.get("cookies_from_browser"),
                                          cookies_file=tc.get("cookies_file"))
    except Exception:
        log.exception("transcription job failed")
        return
    if n:
        process_pending(cfg, db, llm)


def process_pending(cfg: Config, db: DB, llm: LLM) -> int:
    """Extract levels/macro from pending items. Safe to call from several threads."""
    if not _process_lock.acquire(blocking=False):
        return 0  # another thread is already working through the queue
    done = 0
    try:
        while items := db.pending_items():
            for item in items:
                try:
                    extraction = llm.extract(item)
                except Exception as e:
                    log.exception("extraction failed for %s", item["id"])
                    db.mark_item(item["id"], "error", str(e)[:500])
                    continue
                db.save_extraction(item, extraction)
                done += 1
                log.info("%s: %d level(s)%s", item["id"], len(extraction.levels),
                         ", macro" if extraction.is_macro else "")
                if extraction.is_macro and cfg.get("macro_alert_immediately", False):
                    try:
                        newsletter.send_macro_alert(cfg, db, item["id"])
                    except Exception:
                        log.exception("macro alert failed for %s", item["id"])
    finally:
        _process_lock.release()
    return done


def run_once(cfg: Config, db: DB, llm: LLM) -> None:
    poll_sources(cfg, db)
    process_pending(cfg, db, llm)
