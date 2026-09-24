from datetime import datetime, timedelta, timezone

import pytest

from market_digest.config import Config, Source
from market_digest.db import DB
from market_digest.models import Brief, Extraction, MacroTheme, PriceLevel


def iso(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def level(ticker, price, level_type="support", direction="bullish", timeframe="swing", **kw):
    return PriceLevel(ticker=ticker, level_type=level_type, price=price, price_high=kw.get("price_high"),
                      direction=direction, timeframe=timeframe, conviction="medium",
                      note=kw.get("note", f"{ticker} {price} 附近"), quote=kw.get("quote", str(price)))


def extraction(levels=(), macro=None, risk=None):
    return Extraction(summary="摘要", levels=list(levels), is_macro=macro is not None,
                      macro_summary=macro, risk_level=risk,
                      macro_themes=[MacroTheme(topic="rates", view="降息推迟", market_impact="压制估值")] if macro else [])


def add(db, item_id, author, kind="youtube", hours_ago=1, ext=None, category="levels"):
    item = dict(id=item_id, kind=kind, author=author, category=category, title=f"{author} 标题",
                url=f"https://example.com/{item_id}", content="...", published_at=iso(hours_ago))
    db.add_item(**item)
    if ext is not None:
        db.save_extraction(item, ext)
    return item


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "test.db")


@pytest.fixture
def cfg():
    return Config(raw={"timezone": "America/New_York", "cluster_tolerance_pct": 0.6},
                  sources=[Source(kind="youtube", name="A"), Source(kind="x", name="B"),
                           Source(kind="discord", name="D", match=["Trading Room", "Mike"]),
                           Source(kind="website", name="W", url="https://example.com")])


class FakeLLM:
    def __init__(self, ext=None):
        self.ext = ext or extraction()
        self.calls = []

    def extract(self, item):
        self.calls.append(item["id"])
        return self.ext

    def brief(self, kind, data):
        self.last_brief_data = data
        return Brief(headline="SPY 580 是多方共识支撑", overview="概述", focus=["SPY 580"], disagreements=[])
