"""New videos come from each channel's public RSS feed; subtitles via youtube-transcript-api.

Auto-generated subtitles usually appear 10-60 minutes after upload (longer for live streams), so a
video without subtitles is stored as 'waiting' and retried on every poll. If the channel has
subtitles turned off, or none appear within SUBTITLE_WAIT, the video is queued as 'transcribe':
its audio is downloaded and transcribed locally with Whisper (see transcribe.py).
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import feedparser
import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript, NoTranscriptFound, TranscriptsDisabled, VideoUnavailable,
)

from ..db import DB

log = logging.getLogger(__name__)

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
LANGUAGES = ("en", "en-US", "zh-Hans", "zh-Hant", "zh", "zh-CN", "zh-TW")
SUBTITLE_WAIT = timedelta(minutes=90)
WAIT_HOURS = 12  # give up if neither subtitles nor transcription produce text


def resolve_channel_id(handle_or_url: str) -> str:
    """Turn '@SomeChannel' or a channel URL into its UC... channel id."""
    url = handle_or_url
    if not url.startswith("http"):
        url = f"https://www.youtube.com/{url if url.startswith('@') else '@' + url}"
    html = httpx.get(url, follow_redirects=True, timeout=20,
                     headers={"Accept-Language": "en-US"}).text
    m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', html)
    if not m:
        raise ValueError(f"could not find a channel id at {url}")
    return m.group(1)


def _proxy_config():
    """YouTube blocks subtitle requests from most cloud-server IPs; route them through a
    residential proxy when running on a VPS."""
    if os.environ.get("WEBSHARE_PROXY_USERNAME"):
        return WebshareProxyConfig(proxy_username=os.environ["WEBSHARE_PROXY_USERNAME"],
                                   proxy_password=os.environ["WEBSHARE_PROXY_PASSWORD"])
    if proxy := os.environ.get("YOUTUBE_PROXY"):
        return GenericProxyConfig(http_url=proxy, https_url=proxy)
    return None


class SubtitlesDisabled(Exception):
    """The uploader turned subtitles off; waiting will not help."""


def fetch_transcript(video_id: str) -> str | None:
    """Return the transcript text, or None if subtitles are not available (yet)."""
    api = YouTubeTranscriptApi(proxy_config=_proxy_config())
    try:
        fetched = api.fetch(video_id, languages=LANGUAGES)
    except NoTranscriptFound:
        # Fall back to whatever language exists (e.g. only a Korean auto-caption track).
        try:
            transcript = next(iter(api.list(video_id)))
            fetched = transcript.fetch()
        except (StopIteration, CouldNotRetrieveTranscript):
            return None
    except TranscriptsDisabled:
        raise SubtitlesDisabled(video_id)
    except VideoUnavailable:
        return None
    return " ".join(s.text.replace("\n", " ") for s in fetched)


def _from_feed(channel_id: str) -> list[dict] | None:
    r = httpx.get(FEED_URL.format(channel_id), timeout=20)
    if r.status_code != 200:
        return None
    feed = feedparser.parse(r.text)
    return [{"id": e.get("yt_videoid") or e.id.rsplit(":", 1)[-1], "title": e.title,
             "published": datetime(*e.published_parsed[:6], tzinfo=timezone.utc)}
            for e in feed.entries]


def _from_channel_page(channel_id: str) -> list[dict]:
    """Fallback when the RSS feed is down: newest ids from the channel's Videos and Live tabs.
    The page has no exact upload time, so `published` is None (discovery time is used)."""
    ids: list[str] = []
    for tab in ("videos", "streams"):
        r = httpx.get(f"https://www.youtube.com/channel/{channel_id}/{tab}", timeout=20,
                      follow_redirects=True, headers={"Accept-Language": "en-US"})
        if r.status_code == 200:
            ids += re.findall(r'"videoId":"([\w-]{11})"', r.text)
    return [{"id": v, "title": None, "published": None} for v in list(dict.fromkeys(ids))[:15]]


def _title(video_id: str) -> str:
    r = httpx.get("https://www.youtube.com/oembed", timeout=20,
                  params={"format": "json", "url": f"https://www.youtube.com/watch?v={video_id}"})
    return r.json().get("title", video_id) if r.status_code == 200 else video_id


def poll(db: DB, sources, transcribe: bool = True) -> int:
    """Discover new videos and fill in subtitles for waiting ones. Returns items made ready."""
    now = datetime.now(timezone.utc)
    for src in sources:
        try:
            videos = _from_feed(src.channel_id)
            if videos is None:
                log.info("youtube feed unavailable for %s, reading channel page", src.name)
                videos = _from_channel_page(src.channel_id)
        except httpx.HTTPError as e:
            log.warning("youtube discovery failed for %s: %s", src.name, e)
            continue
        first_scan = db.get_kv(f"youtube:scanned:{src.channel_id}") is None
        for v in videos:
            item_id = f"youtube:{v['id']}"
            if db.has_item(item_id):
                continue
            published = v["published"] or now
            # The first time a channel is added, don't process its whole backlog.
            backlog = (now - published > timedelta(days=3)) or (first_scan and v["published"] is None)
            db.add_item(id=item_id, kind="youtube", author=src.name, category=src.category,
                        title=v["title"] or ("" if backlog else _title(v["id"])),
                        url=f"https://www.youtube.com/watch?v={v['id']}", content=None,
                        published_at=published.isoformat(timespec="seconds"))
            db.mark_item(item_id, "skipped" if backlog else "waiting")
            if not backlog:
                log.info("new video from %s: %s", src.name, v["title"] or v["id"])
        db.set_kv(f"youtube:scanned:{src.channel_id}", now.isoformat())

    ready = 0
    for item in db.query("SELECT * FROM items WHERE kind='youtube' AND status='waiting'"):
        video_id = item["id"].split(":", 1)[1]
        waited = now - datetime.fromisoformat(item["fetched_at"])
        try:
            text = fetch_transcript(video_id)
        except SubtitlesDisabled:
            text, waited = None, SUBTITLE_WAIT
        except Exception as e:  # network errors, IP blocks: retry next poll
            log.warning("transcript fetch failed for %s: %s", video_id, " ".join(str(e).split())[:160])
            continue
        if text:
            db.execute("UPDATE items SET content=?, status='pending' WHERE id=?", (text, item["id"]))
            ready += 1
        elif transcribe and waited >= SUBTITLE_WAIT:
            log.info("no subtitles for %s, queueing audio transcription", video_id)
            db.mark_item(item["id"], "transcribe")
        elif waited > timedelta(hours=WAIT_HOURS):
            db.mark_item(item["id"], "error", "no subtitles after waiting")
    return ready
