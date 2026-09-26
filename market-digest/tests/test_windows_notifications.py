import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from market_digest.sources import windows_notifications as wn


def toast(nid, app, *texts, minutes_ago=0):
    binding = NS(get_text_elements=lambda: [NS(text=t) for t in texts])
    return NS(id=nid, creation_time=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
              app_info=NS(display_info=NS(display_name=app)),
              notification=NS(visual=NS(get_binding=lambda name: binding)))


class Listener:
    def __init__(self, toasts):
        self.toasts = toasts

    async def get_notifications_async(self, kinds):
        return self.toasts


def run(listener, db, cfg, seen=None, started=None):
    return asyncio.run(wn.poll_once(listener, "toast", seen if seen is not None else set(),
                                    started or datetime.now(timezone.utc), db, cfg.by_kind("discord")))


def test_discord_toasts_matching_a_source_are_stored_once(db, cfg):
    listener = Listener([
        toast(1, "Discord", "Mike (#levels, Trading Room)", "SPY 580 support, 595 target"),
        toast(2, "Outlook", "Mike Trading Room", "meeting at 5"),           # other app
        toast(3, "Discord", "Anna (#general, Other Server)", "QQQ 500"),     # unmatched server
    ])
    seen = set()
    assert run(listener, db, cfg, seen) == 1
    item = db.pending_items()[0]
    assert item["author"] == "D" and "SPY 580 support" in item["content"]
    assert run(listener, db, cfg, seen) == 0  # same toasts on the next poll are skipped


def test_old_toasts_from_before_start_are_ignored(db, cfg):
    listener = Listener([toast(1, "Discord", "Mike (Trading Room)", "SPY 570", minutes_ago=240)])
    assert run(listener, db, cfg) == 0


def test_not_available_off_windows():
    assert wn.available() is False
