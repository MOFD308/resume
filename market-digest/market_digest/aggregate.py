"""Turn raw extracted levels into the per-ticker view used by the dashboard and newsletters."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .db import DB

DEFAULT_TTL = {"intraday": 1, "swing": 10, "long_term": 60, "unspecified": 5}


def _age_limit(timeframe: str, ttl: dict) -> timedelta:
    days = ttl.get(timeframe, ttl.get("unspecified", 5))
    # Intraday calls stay visible through the next session (weekend-safe).
    return timedelta(days=days + (2 if timeframe == "intraday" else 0))


def active_levels(db: DB, ttl: dict | None = None, now: datetime | None = None) -> list[dict]:
    """Levels still in force: not expired, and not superseded by a newer call from the same author.

    - Website pages are snapshots, so only the newest snapshot per site counts.
    - For other sources, if an author re-states the same ticker/type/price, keep the newest.
    """
    ttl = {**DEFAULT_TTL, **(ttl or {})}
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=max(ttl.values()) + 2)
    rows = db.query("SELECT l.*, i.url AS item_url, i.title AS item_title FROM levels l "
                    "JOIN items i ON i.id = l.item_id WHERE l.published_at >= ? "
                    "ORDER BY l.published_at DESC", (horizon.isoformat(),))

    latest_snapshot: dict[str, str] = {}
    for r in rows:
        if r["kind"] == "website":
            latest_snapshot.setdefault(r["author"], r["item_id"])

    seen, out = set(), []
    for r in rows:
        if r["kind"] == "website" and latest_snapshot[r["author"]] != r["item_id"]:
            continue
        if now - datetime.fromisoformat(r["published_at"]) > _age_limit(r["timeframe"], ttl):
            continue
        key = (r["author"], r["ticker"], r["level_type"], round(r["price"], 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def cluster(levels: list[dict], tolerance_pct: float = 0.6) -> list[dict]:
    """Group one ticker's levels whose prices are within tolerance_pct of each other."""
    groups: list[list[dict]] = []
    for lv in sorted(levels, key=lambda l: l["price"]):
        if groups and abs(lv["price"] - groups[-1][0]["price"]) / groups[-1][0]["price"] * 100 <= tolerance_pct:
            groups[-1].append(lv)
        else:
            groups.append([lv])
    out = []
    for g in groups:
        authors = sorted({l["author"] for l in g})
        types = sorted({l["level_type"] for l in g})
        out.append({
            "price": round(sum(l["price"] for l in g) / len(g), 2),
            "low": min(l["price"] for l in g),
            "high": max((l["price_high"] or l["price"]) for l in g),
            "types": types,
            "authors": authors,
            "consensus": len(authors),
            "latest": max(l["published_at"] for l in g),
            "calls": g,
        })
    return out


def build_board(db: DB, prices: dict[str, float | None], ttl: dict | None = None,
                tolerance_pct: float = 0.6) -> list[dict]:
    """One row per ticker with clustered levels, nearest support/resistance and consensus."""
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for lv in active_levels(db, ttl):
        by_ticker[lv["ticker"]].append(lv)

    board = []
    for ticker, levels in by_ticker.items():
        clusters = cluster(levels, tolerance_pct)
        price = prices.get(ticker)
        for c in clusters:
            c["distance_pct"] = round((c["price"] - price) / price * 100, 2) if price else None
            c["side"] = None if price is None else ("above" if c["price"] >= price else "below")
        below = [c for c in clusters if c["side"] == "below"]
        above = [c for c in clusters if c["side"] == "above"]
        board.append({
            "ticker": ticker,
            "price": price,
            "clusters": clusters,
            "nearest_below": below[-1] if below else None,
            "nearest_above": above[0] if above else None,
            "authors": sorted({l["author"] for l in levels}),
            "max_consensus": max(c["consensus"] for c in clusters),
            "latest": max(l["published_at"] for l in levels),
            "bias": _bias(levels),
        })
    board.sort(key=lambda r: (len(r["authors"]), r["latest"]), reverse=True)
    return board


def _bias(levels: list[dict]) -> str:
    score = sum({"bullish": 1, "bearish": -1}.get(l["direction"], 0) for l in levels)
    return "bullish" if score > 0 else "bearish" if score < 0 else "neutral"


def source_stats(db: DB, days: int = 7) -> list[dict]:
    """Activity and focus per author over the last `days` days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    items = db.query("SELECT author, kind, COUNT(*) n, SUM(is_macro) macro, MAX(published_at) last "
                     "FROM items WHERE published_at >= ? AND status='done' GROUP BY author, kind", (since,))
    levels = db.query("SELECT author, ticker, COUNT(*) n FROM levels WHERE published_at >= ? "
                      "GROUP BY author, ticker ORDER BY n DESC", (since,))
    top: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for r in levels:
        counts[r["author"]] += r["n"]
        if len(top[r["author"]]) < 5:
            top[r["author"]].append(r["ticker"])
    return [{**r, "levels": counts.get(r["author"], 0), "top_tickers": top.get(r["author"], [])}
            for r in items]


def macro_items(db: DB, since: datetime) -> list[dict]:
    return db.query("SELECT id, kind, author, title, url, published_at, macro_summary, risk_level, "
                    "macro_themes FROM items WHERE is_macro=1 AND status='done' AND published_at >= ? "
                    "ORDER BY published_at DESC", (since.isoformat(),))
