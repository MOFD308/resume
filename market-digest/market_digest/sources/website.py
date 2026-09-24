"""Bloggers' own pages that list their current levels.

Each poll downloads the page, strips it to text and compares a hash with the last version. When it
changed, the whole page becomes a new item. The dashboard only uses levels from the newest version
of each page, so levels the blogger removed disappear too.
"""
import hashlib
import logging
import os
from datetime import datetime, timezone

import httpx

from ..db import DB
from .text import html_to_text

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/128.0 Safari/537.36"}


def poll(db: DB, sources) -> int:
    added = 0
    for src in sources:
        headers = dict(HEADERS)
        if src.cookie_env and os.environ.get(src.cookie_env):
            headers["Cookie"] = os.environ[src.cookie_env]
        try:
            r = httpx.get(src.url, headers=headers, follow_redirects=True, timeout=30)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("website fetch failed for %s: %s", src.name, e)
            continue
        text = html_to_text(r.text)
        if len(text) < 50:
            log.warning("%s returned almost no text; the page may need a login or JavaScript", src.name)
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        key = f"website:hash:{src.name}"
        if db.get_kv(key) == digest:
            continue
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if db.add_item(id=f"website:{src.name}:{digest}", kind="website", author=src.name,
                       category=src.category, title=f"{src.name} 网站更新", url=src.url,
                       content=text, published_at=now):
            added += 1
            log.info("website %s changed", src.name)
        db.set_kv(key, digest)
    return added
