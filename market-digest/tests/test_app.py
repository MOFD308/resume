from fastapi.testclient import TestClient

from market_digest import newsletter, pipeline, prices
from market_digest.dashboard import create_app

from .conftest import FakeLLM, add, extraction, level


def test_process_pending_saves_levels_and_sends_macro_alert(db, cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(newsletter, "send_email", lambda subject, html, text="": sent.append((subject, html)))
    cfg.raw["macro_alert_immediately"] = True
    add(db, "youtube:m1", "A", category="macro")
    llm = FakeLLM(extraction([level("TLT", 90)], macro="- 美联储推迟降息", risk="elevated"))

    assert pipeline.process_pending(cfg, db, llm) == 1
    assert db.query("SELECT status FROM items")[0]["status"] == "done"
    assert db.query("SELECT ticker FROM levels")[0]["ticker"] == "TLT"
    assert len(sent) == 1 and "宏观更新" in sent[0][0] and "美联储推迟降息" in sent[0][1]
    newsletter.send_macro_alert(cfg, db, "youtube:m1")  # already sent: no duplicate
    assert len(sent) == 1


def test_macro_items_do_not_email_immediately_by_default(db, cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(newsletter, "send_email", lambda subject, html, text="": sent.append(subject))
    add(db, "x:m1", "B", kind="x")
    pipeline.process_pending(cfg, db, FakeLLM(extraction([], macro="- 地缘风险上升", risk="high")))
    assert sent == []


def test_daily_macro_digest_combines_all_sources(db, cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(newsletter, "send_email", lambda subject, html, text="": sent.append((subject, html)))
    add(db, "youtube:m1", "A", hours_ago=5, ext=extraction([], macro="- 美联储推迟降息", risk="elevated"))
    add(db, "x:m2", "B", kind="x", hours_ago=2, ext=extraction([], macro="- 油价上涨推高通胀", risk="high"))
    add(db, "x:old", "B", kind="x", hours_ago=30, ext=extraction([], macro="- 旧观点", risk="low"))
    add(db, "youtube:lv", "A", hours_ago=1, ext=extraction([level("SPY", 580)]))  # levels only: not macro
    llm = FakeLLM()

    newsletter.send(cfg, db, llm, "macro_daily")
    authors = [m["author"] for m in llm.last_brief_data["macro"]]
    assert sorted(authors) == ["A", "B"] and "by theme" in llm.last_instructions
    subject, html = sent[0]
    assert subject.startswith("【每日宏观总结】")
    assert "美联储推迟降息" in html and "油价上涨推高通胀" in html and "旧观点" not in html
    assert "点位总表" not in html

    newsletter.send(cfg, db, llm, "macro_daily")  # nothing new since the last digest
    assert sent[1][0] == "【每日宏观总结】暂无新内容"


def test_failed_extraction_is_marked_error(db, cfg):
    add(db, "youtube:bad", "A")

    class Boom(FakeLLM):
        def extract(self, item):
            raise RuntimeError("boom")

    pipeline.process_pending(cfg, db, Boom())
    assert db.query("SELECT status, error FROM items")[0] == {"status": "error", "error": "boom"}


def test_newsletter_renders_board_and_brief(db, cfg, monkeypatch):
    monkeypatch.setattr(prices, "get_prices", lambda tickers: {t: 585.0 for t in tickers})
    add(db, "youtube:1", "A", ext=extraction([level("SPY", 580, note="多方防守位")]))
    add(db, "x:1", "B", kind="x", ext=extraction([level("SPY", 581)]))
    llm = FakeLLM()
    subject, html, _ = newsletter.build(cfg, db, llm, "premarket")
    assert subject == "【盘前点位】SPY 580 是多方共识支撑"
    assert "多方防守位" in html and "×2" in html
    near = llm.last_brief_data["levels_near_price"][0]
    assert near["ticker"] == "SPY" and near["last_price"] == 585.0 and near["authors"] == ["A", "B"]


def test_dashboard_api_and_android_ingest(db, cfg, monkeypatch):
    monkeypatch.setattr(prices, "get_prices", lambda tickers: {t: 100.0 for t in tickers})
    monkeypatch.setenv("INGEST_TOKEN", "secret")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    add(db, "youtube:1", "A", ext=extraction([level("AMD", 95)]))
    triggered = []
    client = TestClient(create_app(cfg, db, on_new_item=lambda: triggered.append(1)))

    assert "点位看板" in client.get("/").text
    rows = client.get("/api/board").json()["rows"]
    assert rows[0]["ticker"] == "AMD" and rows[0]["nearest_below"]["distance_pct"] == -5.0
    assert client.get("/api/feed").json()[0]["levels"] == 1

    payload = {"package": "com.discord", "title": "Mike · Trading Room", "text": "NVDA 120 breakout"}
    assert client.post("/ingest/android", json=payload).status_code == 403
    r = client.post("/ingest/android?token=secret", json=payload)
    assert r.json()["status"] == "stored" and triggered == [1]


def test_dashboard_password(db, cfg, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    client = TestClient(create_app(cfg, db))
    assert client.get("/api/feed").status_code == 401
    assert client.get("/api/feed", auth=("me", "pw")).status_code == 200


def test_level_email_lists_all_levels_and_highlights_changes(db, cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(newsletter, "send_email", lambda subject, html, text="": sent.append(html))
    monkeypatch.setattr(prices, "get_prices", lambda tickers: {"SPY": 585.0, "NVDA": 120.0, "AMD": 150.0})
    add(db, "youtube:1", "A", hours_ago=10, ext=extraction([level("SPY", 580), level("NVDA", 110)]))
    add(db, "x:1", "B", kind="x", hours_ago=10, ext=extraction([level("AMD", 140, note="要被撤掉")]))

    newsletter.send(cfg, db, FakeLLM(), "premarket")
    assert "第一封点位邮件" in sent[0]

    # A moves SPY support 580 -> 582 and adds an NVDA target; B's AMD call disappears.
    add(db, "youtube:2", "A", hours_ago=1,
        ext=extraction([level("SPY", 582, note="上移支撑"), level("NVDA", 110), level("NVDA", 130, "target")]))
    db.execute("DELETE FROM levels WHERE item_id='x:1'")
    db.execute("DELETE FROM levels WHERE item_id='youtube:1' AND ticker='SPY'")
    llm = FakeLLM()
    newsletter.send(cfg, db, llm, "midday")
    html = sent[1]

    changes = {(c["ticker"], c["change"]): c for c in llm.last_brief_data["changes_since_last_email"]}
    assert changes[("SPY", "moved")]["old_price"] == 580 and changes[("SPY", "moved")]["price"] == 582
    assert ("NVDA", "new") in changes and ("AMD", "removed") in changes
    assert ("NVDA", "moved") not in changes  # the unchanged 110 support is not flagged
    assert "本次变化" in html and "调整" in html and "移除" in html and "上移支撑" in html
    assert "全部点位" in html and "NVDA" in html and "110" in html  # every level is still listed

    newsletter.send(cfg, db, FakeLLM(), "close")
    assert "点位没有变化" in sent[2]


def test_preview_does_not_move_the_change_baseline(db, cfg, monkeypatch):
    monkeypatch.setattr(prices, "get_prices", lambda tickers: {})
    add(db, "youtube:1", "A", ext=extraction([level("SPY", 580)]))
    newsletter.build(cfg, db, FakeLLM(), "premarket")
    assert db.get_kv(newsletter.SNAPSHOT_KEY) is None
