"""Scheduled newsletters and optional instant macro alerts.

Level newsletters (premarket / midday / close) cover price levels; macro newsletters (daily /
weekly) synthesise the macro views from every source into one read.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import aggregate, prices
from .config import Config
from .db import DB, utcnow
from .llm import LLM
from .mailer import send_email

log = logging.getLogger(__name__)

TITLES = {"premarket": "盘前点位", "midday": "盘中更新", "close": "收盘复盘",
          "macro_daily": "每日宏观总结", "weekly": "宏观周报"}
LEVEL_KINDS = ("premarket", "midday", "close")
MACRO_KINDS = ("macro_daily", "weekly")

MACRO_INSTRUCTIONS = """This is a macro digest. In `overview`, organise the views by theme (e.g. rates \
and the Fed, inflation and jobs, geopolitics and war, fiscal/liquidity, earnings) with a short \
paragraph per theme, naming which author holds which view, then end with the overall risk picture. \
`focus` lists upcoming events or data the authors say to watch and key risks; `disagreements` lists \
where the authors disagree."""
LEVEL_TYPES = {"support": "支撑", "resistance": "阻力", "target": "目标", "stop": "止损",
               "entry": "入场", "pivot": "多空分界", "other": "其他"}
RISK = {"low": "低", "moderate": "中", "elevated": "偏高", "high": "高"}
KIND = {"youtube": "YouTube", "x": "X", "website": "网站", "discord": "Discord"}

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                  autoescape=select_autoescape(["html"]))
env.filters["ltype"] = lambda t: LEVEL_TYPES.get(t, t)
env.filters["risk"] = lambda r: RISK.get(r, r or "")
env.filters["kind"] = lambda k: KIND.get(k, k)
env.filters["px"] = lambda v: "" if v is None else f"{v:,.2f}".rstrip("0").rstrip(".")
TOPICS = {"rates": "利率", "fed": "美联储", "inflation": "通胀", "jobs": "就业", "geopolitics": "地缘政治",
          "war": "战争", "fiscal": "财政", "liquidity": "流动性", "earnings": "财报", "credit": "信用",
          "fx": "汇率", "commodities": "大宗商品", "china": "中国", "other": "其他"}
env.filters["topic"] = lambda t: TOPICS.get(t, t)
env.filters["fromjson"] = lambda s: json.loads(s or "[]")


def _local(cfg: Config, iso: str) -> str:
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%m-%d %H:%M")


def _near(board: list[dict], pct: float = 1.0) -> list[dict]:
    """Clusters within pct% of the current price - the levels in play right now."""
    out = []
    for row in board:
        for c in row["clusters"]:
            if c["distance_pct"] is not None and abs(c["distance_pct"]) <= pct:
                out.append({**c, "ticker": row["ticker"], "last_price": row["price"]})
    return sorted(out, key=lambda c: abs(c["distance_pct"]))


def _compact_board(board: list[dict], limit: int = 30) -> list[dict]:
    """Smaller version of the board to send to Claude."""
    return [{
        "ticker": r["ticker"], "price": r["price"], "bias": r["bias"], "sources": r["authors"],
        "levels": [{"price": c["price"], "types": c["types"], "authors": c["authors"],
                    "distance_pct": c["distance_pct"],
                    "notes": [f'{l["author"]}: {l["note"]}' for l in c["calls"]][:4]}
                   for c in r["clusters"]],
    } for r in board[:limit]]


def _since(db: DB, key: str, default: timedelta) -> datetime:
    last = db.get_kv(key)
    return datetime.fromisoformat(last) if last else datetime.now(timezone.utc) - default


SNAPSHOT_KEY = "levels:last_email_snapshot"


def _level_changes(db: DB, board: list[dict], active: list[dict]) -> dict:
    """Mark calls that are new or moved since the last level email, and collect removed ones."""
    raw = db.get_kv(SNAPSHOT_KEY)
    if raw is None:
        return {"first_email": True, "changes_list": [], "removed": []}
    changes, removed = aggregate.diff_levels(json.loads(raw), active)
    removed_tickers = {l["ticker"] for l in removed}
    changes_list = []
    for row in board:
        for c in row["clusters"]:
            for call in c["calls"]:
                call.update(changes.get(call["id"], {"change": None, "old_price": None}))
                if call["change"]:
                    changes_list.append(call)
            c["changed"] = any(call["change"] for call in c["calls"])
        row["changed"] = any(c["changed"] for c in row["clusters"]) or row["ticker"] in removed_tickers
    # Changed tickers first, otherwise keep the usual order (most authors first).
    board.sort(key=lambda r: not r["changed"])
    changes_list.sort(key=lambda l: (l["change"] != "new", l["ticker"], -l["price"]))
    return {"first_email": False, "changes_list": changes_list, "removed": removed}


def build(cfg: Config, db: DB, llm: LLM, kind: str) -> tuple[str, str, list | None]:
    """Render one newsletter. Returns (subject, html, level snapshot to store once it is sent)."""
    now = datetime.now(timezone.utc)
    board, near, macro, stats, snap = [], [], [], [], None
    extra = {"first_email": False, "changes_list": [], "removed": []}
    if kind in LEVEL_KINDS:
        ttl = cfg.get("level_ttl_days")
        active = aggregate.active_levels(db, ttl)
        board = aggregate.build_board(db, prices.get_prices(sorted({l["ticker"] for l in active})), ttl,
                                      cfg.get("cluster_tolerance_pct", 0.6))
        extra = _level_changes(db, board, active)
        near = _near(board)[:20]
        snap = aggregate.snapshot(active)
        moved = sum(1 for l in extra["changes_list"] if l["change"] == "moved")
        stats = [("标的", len(board), None), ("点位", len(active), None),
                 ("新增", len(extra["changes_list"]) - moved, "#b7791f"), ("调整", moved, "#6b46c1"),
                 ("移除", len(extra["removed"]), "#8a92a0")]
        data = {
            "newsletter": TITLES[kind],
            "board": _compact_board(board),
            "changes_since_last_email": [
                {"ticker": l["ticker"], "author": l["author"], "type": l["level_type"], "change": l["change"],
                 "price": l["price"], "old_price": l["old_price"], "note": l["note"]}
                for l in extra["changes_list"]] + [
                {"ticker": l["ticker"], "author": l["author"], "type": l["level_type"], "change": "removed",
                 "price": l["price"]} for l in extra["removed"]],
            "levels_near_price": [{"ticker": c["ticker"], "last_price": c["last_price"], "level": c["price"],
                                   "types": c["types"], "authors": c["authors"],
                                   "distance_pct": c["distance_pct"]} for c in near],
        }
        instructions = ("Lead the overview with what changed since the last email (new, moved and "
                        "removed levels), then the levels closest to the current price.")
    else:
        since = (now - timedelta(days=7) if kind == "weekly"
                 else _since(db, "newsletter:macro_daily:last_sent", timedelta(hours=24)))
        macro = aggregate.macro_items(db, since)
        risks = [m["risk_level"] for m in macro if m["risk_level"]]
        stats = [("宏观内容", len(macro), None), ("博主", len({m["author"] for m in macro}), None),
                 ("风险偏高/高", sum(r in ("elevated", "high") for r in risks), "#d64545")] if macro else []
        data = {
            "newsletter": TITLES[kind],
            "period_since": since.isoformat(),
            "macro": [{"author": m["author"], "source": m["kind"], "title": m["title"],
                       "published": m["published_at"], "risk": m["risk_level"],
                       "summary": m["macro_summary"], "themes": json.loads(m["macro_themes"] or "[]")}
                      for m in macro],
        }
        instructions = MACRO_INSTRUCTIONS

    try:
        brief = llm.brief(kind, data, instructions) if (board or macro) else None
    except Exception:
        log.exception("brief generation failed; sending without it")
        brief = None

    html = env.get_template("newsletter.html").render(
        title=TITLES[kind], kind=kind, brief=brief, board=board, near=near, macro=macro, stats=stats,
        dashboard_url=cfg.get("dashboard_url"), **extra,
        date=now.astimezone(ZoneInfo(cfg.get("timezone", "America/New_York"))).strftime("%Y-%m-%d"),
        local=lambda iso: _local(cfg, iso),
    )
    fallback = "暂无新内容" if not (board or macro) else now.strftime("%Y-%m-%d")
    subject = f"【{TITLES[kind]}】" + (brief.headline if brief else fallback)
    return subject, html, snap


def send(cfg: Config, db: DB, llm: LLM, kind: str) -> None:
    subject, html, snap = build(cfg, db, llm, kind)
    send_email(subject, html)
    if kind in LEVEL_KINDS:
        db.set_kv(SNAPSHOT_KEY, json.dumps(snap, ensure_ascii=False))
    elif kind == "macro_daily":
        db.set_kv("newsletter:macro_daily:last_sent", utcnow())


def send_macro_alert(cfg: Config, db: DB, item_id: str) -> None:
    key = f"macro_alert:{item_id}"
    if db.get_kv(key):
        return
    item = db.query("SELECT * FROM items WHERE id=?", (item_id,))[0]
    levels = db.query("SELECT * FROM levels WHERE item_id=? ORDER BY ticker, price", (item_id,))
    html = env.get_template("macro_alert.html").render(
        item=item, levels=levels, dashboard_url=cfg.get("dashboard_url"),
        local=lambda iso: _local(cfg, iso))
    risk = RISK.get(item["risk_level"], "")
    send_email(f"【宏观更新】{item['author']}：{item['title'] or ''}"
               + (f"（风险：{risk}）" if risk else ""), html)
    db.set_kv(key, utcnow())
