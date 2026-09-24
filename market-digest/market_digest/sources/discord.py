"""Discord messages, from two routes that need no access to the blogger's server:

1. Android notification forwarding: a phone app (MacroDroid/Tasker) POSTs each Discord notification
   to /ingest/android on the dashboard server. `ingest_notification` matches it to a configured
   source by keywords (server / channel / author name) found in the notification.
2. Manual forwarding: you forward or paste messages/screenshots into a channel of your OWN server;
   a bot you own reads that channel (`run_bot`). Screenshots are passed to Claude as images.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from ..db import DB

log = logging.getLogger(__name__)

DISCORD_PACKAGES = {"com.discord", "com.discord.alpha", "com.discord.beta"}
DEDUPE_HOURS = 24


def _has_number(text: str) -> bool:
    return bool(re.search(r"\d", text))


def _new_lines(db: DB, source_name: str, text: str) -> list[str]:
    """Android groups Discord notifications and re-sends earlier lines; keep only unseen ones."""
    fresh = []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=DEDUPE_HOURS)).isoformat()
    db.execute("DELETE FROM kv WHERE key LIKE 'dn:%' AND value < ?", (cutoff,))
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        key = "dn:" + hashlib.sha1(f"{source_name}|{line}".encode()).hexdigest()
        if db.get_kv(key) is None:
            db.set_kv(key, datetime.now(timezone.utc).isoformat())
            fresh.append(line)
    return fresh


def match_source(sources, title: str, text: str):
    haystack = f"{title}\n{text}".lower()
    for src in sources:
        if src.match and all(k.lower() in haystack for k in src.match):
            return src
    return None


def ingest_notification(db: DB, sources, payload: dict) -> dict:
    """Store one forwarded Android notification. Returns a small status dict for the phone app."""
    package = (payload.get("package") or payload.get("app") or "").strip()
    if package and package not in DISCORD_PACKAGES and "discord" not in package.lower():
        return {"status": "ignored", "reason": "not a Discord notification"}
    title = (payload.get("title") or "").strip()
    text = (payload.get("bigtext") or payload.get("text") or "").strip()
    src = match_source(sources, title, text)
    if src is None:
        return {"status": "ignored", "reason": "no configured source matches"}
    lines = _new_lines(db, src.name, text)
    if not lines:
        return {"status": "duplicate"}
    content = "\n".join(lines)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    item_id = "discord:n:" + hashlib.sha1(f"{src.name}|{now}|{content}".encode()).hexdigest()[:16]
    db.add_item(id=item_id, kind="discord", author=src.name, category=src.category,
                title=title, url=None, content=f"{title}\n{content}", published_at=now)
    if not _has_number(content) and src.category == "levels":
        db.mark_item(item_id, "skipped", "no numbers in message")
        return {"status": "skipped"}
    return {"status": "stored", "id": item_id}


def run_bot(db: DB, sources, on_new_item=None) -> None:
    """Blocking: run the Discord bot that reads your own server's forwarding channels."""
    import discord

    token = os.environ.get("DISCORD_BOT_TOKEN")
    by_channel = {str(s.channel_id): s for s in sources if s.channel_id}
    if not token or not by_channel:
        log.info("Discord bot not started (no DISCORD_BOT_TOKEN or no channel_id configured)")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info("Discord bot logged in as %s, watching %d channel(s)", client.user, len(by_channel))

    @client.event
    async def on_message(message: "discord.Message"):
        src = by_channel.get(str(message.channel.id))
        if src is None or message.author == client.user:
            return
        if src.author_ids and str(message.author.id) not in src.author_ids:
            return
        parts, images = [message.content], []
        # Messages forwarded with Discord's "Forward" button arrive as snapshots.
        for snap in getattr(message, "message_snapshots", []) or []:
            parts.append(snap.content)
            images += [a.url for a in snap.attachments if (a.content_type or "").startswith("image/")]
        images += [a.url for a in message.attachments if (a.content_type or "").startswith("image/")]
        content = "\n".join(p for p in parts if p).strip()
        if not content and not images:
            return
        db.add_item(id=f"discord:{message.id}", kind="discord", author=src.name,
                    category=src.category, title=f"#{message.channel}", url=message.jump_url,
                    content=content, published_at=message.created_at.isoformat(timespec="seconds"),
                    images=json.dumps(images) if images else None)
        if on_new_item:
            await asyncio.to_thread(on_new_item)

    client.run(token, log_handler=None)
