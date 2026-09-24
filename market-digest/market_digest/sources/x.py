"""X/Twitter posts.

With X_BEARER_TOKEN set, uses the official pay-per-use API ($0.005 per post read; only posts newer
than the last one seen are requested). Without it, falls back to reading X's "new post"
notification emails from the IMAP inbox, which is free but can miss posts.
"""
import email
import imaplib
import logging
import os
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

import httpx

from ..db import DB
from .text import html_to_text

log = logging.getLogger(__name__)

API = "https://api.x.com/2"


def poll(db: DB, sources, inbox_cfg: dict | None) -> int:
    if not sources:
        return 0
    token = os.environ.get("X_BEARER_TOKEN")
    if token:
        return _poll_api(db, sources, token)
    if inbox_cfg and os.environ.get("GMAIL_APP_PASSWORD"):
        return _poll_email(db, sources, inbox_cfg)
    log.warning("X sources configured but neither X_BEARER_TOKEN nor GMAIL_APP_PASSWORD is set")
    return 0


def _poll_api(db: DB, sources, token: str) -> int:
    added = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        for src in sources:
            handle = src.handle.lstrip("@")
            user_id = db.get_kv(f"x:user_id:{handle}")
            if not user_id:  # one-time lookup, cached forever ($0.01)
                r = http.get(f"/users/by/username/{handle}")
                r.raise_for_status()
                user_id = r.json()["data"]["id"]
                db.set_kv(f"x:user_id:{handle}", user_id)

            since_id = db.get_kv(f"x:since_id:{handle}")
            params = {"max_results": 100 if since_id else 5, "exclude": "retweets",
                      "tweet.fields": "created_at,note_tweet"}
            if since_id:
                params["since_id"] = since_id
            r = http.get(f"/users/{user_id}/tweets", params=params)
            if r.status_code == 429:
                log.warning("X rate limit hit; will retry next poll")
                return added
            r.raise_for_status()
            body = r.json()
            for tweet in body.get("data", []):
                text = (tweet.get("note_tweet") or {}).get("text") or tweet["text"]
                if db.add_item(id=f"x:{tweet['id']}", kind="x", author=src.name,
                               category=src.category, title=f"@{handle}",
                               url=f"https://x.com/{handle}/status/{tweet['id']}", content=text,
                               published_at=tweet["created_at"].replace(".000Z", "+00:00")):
                    added += 1
            if newest := body.get("meta", {}).get("newest_id"):
                db.set_kv(f"x:since_id:{handle}", newest)
    return added


def _decode(value: str | None) -> str:
    return str(make_header(decode_header(value or "")))


def _poll_email(db: DB, sources, cfg: dict) -> int:
    """Read X notification emails and attribute each to a configured handle."""
    handles = {s.handle.lstrip("@").lower(): s for s in sources}
    senders = cfg.get("x_senders") or ["notify@x.com"]
    added = 0
    imap = imaplib.IMAP4_SSL(cfg.get("host", "imap.gmail.com"))
    try:
        imap.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        imap.select(cfg.get("folder", "INBOX"), readonly=True)
        last_uid = int(db.get_kv("x:imap_last_uid", "0"))
        uids: set[int] = set()
        for sender in senders:
            _, data = imap.uid("search", None, f'(FROM "{sender}" SINCE "{_imap_since()}")')
            uids.update(int(u) for u in data[0].split())
        for uid in sorted(u for u in uids if u > last_uid):
            _, msg_data = imap.uid("fetch", str(uid), "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg["Subject"])
            text = _message_text(msg)
            haystack = f"{subject}\n{text}".lower()
            src = next((s for h, s in handles.items() if f"@{h}" in haystack), None)
            if src and db.add_item(id=f"x:email:{uid}", kind="x", author=src.name,
                                   category=src.category, title=subject, url=None,
                                   content=f"{subject}\n\n{text}"[:20000],
                                   published_at=parsedate_to_datetime(msg["Date"]).isoformat()):
                added += 1
            db.set_kv("x:imap_last_uid", str(uid))
    finally:
        imap.logout()
    return added


def _imap_since() -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=2)).strftime("%d-%b-%Y")


def _message_text(msg) -> str:
    plain, html = None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        elif ctype == "text/html" and html is None:
            html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
    text = plain or html_to_text(html or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
