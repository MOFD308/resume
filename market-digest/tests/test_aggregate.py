from market_digest import aggregate

from .conftest import add, extraction, level


def test_levels_from_different_authors_cluster_into_consensus(db):
    add(db, "youtube:1", "A", ext=extraction([level("SPY", 580.0), level("SPY", 600, "resistance", "bearish")]))
    add(db, "x:1", "B", kind="x", ext=extraction([level("SPY", 581.5)]))  # within 0.6% of 580

    board = aggregate.build_board(db, {"SPY": 590.0})
    assert len(board) == 1
    row = board[0]
    assert row["authors"] == ["A", "B"]
    support = row["nearest_below"]
    assert support["consensus"] == 2
    assert support["price"] == 580.75
    assert support["side"] == "below"
    assert row["nearest_above"]["price"] == 600
    assert row["nearest_above"]["distance_pct"] == 1.69


def test_far_apart_levels_stay_separate(db):
    add(db, "youtube:1", "A", ext=extraction([level("NVDA", 100), level("NVDA", 110)]))
    clusters = aggregate.build_board(db, {"NVDA": None})[0]["clusters"]
    assert [c["price"] for c in clusters] == [100, 110]
    assert all(c["side"] is None for c in clusters)


def test_intraday_levels_expire_but_swing_levels_stay(db):
    add(db, "youtube:old", "A", hours_ago=24 * 4,
        ext=extraction([level("QQQ", 500, timeframe="intraday"), level("QQQ", 480, timeframe="swing")]))
    prices = [l["price"] for l in aggregate.active_levels(db)]
    assert prices == [480]


def test_restated_level_keeps_only_newest_call(db):
    add(db, "youtube:1", "A", hours_ago=10, ext=extraction([level("TSLA", 250, note="旧")]))
    add(db, "youtube:2", "A", hours_ago=1, ext=extraction([level("TSLA", 250, note="新")]))
    notes = [l["note"] for l in aggregate.active_levels(db)]
    assert notes == ["新"]


def test_only_latest_website_snapshot_counts(db):
    add(db, "website:W:aaa", "W", kind="website", hours_ago=5, ext=extraction([level("AAPL", 200)]))
    add(db, "website:W:bbb", "W", kind="website", hours_ago=1, ext=extraction([level("AAPL", 210)]))
    assert [l["price"] for l in aggregate.active_levels(db)] == [210]


def test_board_sorted_by_number_of_authors(db):
    add(db, "youtube:1", "A", ext=extraction([level("SPY", 580), level("AMD", 150)]))
    add(db, "x:1", "B", kind="x", ext=extraction([level("SPY", 590)]))
    assert [r["ticker"] for r in aggregate.build_board(db, {})] == ["SPY", "AMD"]


def test_source_stats_counts_levels(db):
    add(db, "youtube:1", "A", ext=extraction([level("SPY", 580), level("SPY", 600), level("AMD", 150)]))
    stats = {s["author"]: s for s in aggregate.source_stats(db)}
    assert stats["A"]["levels"] == 3
    assert stats["A"]["top_tickers"][0] == "SPY"
