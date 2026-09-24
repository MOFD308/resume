from market_digest.sources import discord


def test_matching_notification_is_stored(db, cfg):
    res = discord.ingest_notification(db, cfg.by_kind("discord"), {
        "package": "com.discord", "title": "Mike (#levels, Trading Room)", "text": "SPY 580 support, 595 target"})
    assert res["status"] == "stored"
    item = db.pending_items()[0]
    assert item["author"] == "D" and item["kind"] == "discord"
    assert "SPY 580" in item["content"]


def test_grouped_notification_only_keeps_new_lines(db, cfg):
    srcs = cfg.by_kind("discord")
    base = {"package": "com.discord", "title": "Mike (#levels, Trading Room)"}
    discord.ingest_notification(db, srcs, {**base, "text": "SPY 580 support"})
    res = discord.ingest_notification(db, srcs, {**base, "text": "SPY 580 support\nQQQ 500 resistance"})
    assert res["status"] == "stored"
    newest = db.query("SELECT content FROM items ORDER BY rowid DESC LIMIT 1")[0]["content"]
    assert "QQQ 500" in newest and "SPY 580" not in newest
    assert discord.ingest_notification(db, srcs, {**base, "text": "QQQ 500 resistance"})["status"] == "duplicate"


def test_other_apps_and_unmatched_servers_are_ignored(db, cfg):
    srcs = cfg.by_kind("discord")
    assert discord.ingest_notification(db, srcs, {"package": "com.whatsapp", "title": "Mike Trading Room",
                                                  "text": "1"})["status"] == "ignored"
    assert discord.ingest_notification(db, srcs, {"package": "com.discord", "title": "Other server",
                                                  "text": "SPY 1"})["status"] == "ignored"


def test_messages_without_numbers_are_kept_for_macro_views(db, cfg):
    res = discord.ingest_notification(db, cfg.by_kind("discord"), {
        "package": "com.discord", "title": "Mike (Trading Room)", "text": "Fed sounds hawkish, careful here"})
    assert res["status"] == "stored"
    assert len(db.pending_items()) == 1
