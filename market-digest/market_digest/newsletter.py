"""The emails.

- levels:     sent whenever a level is added or moved; the full board with the changes highlighted.
- premarket / close: before the open and after the close; macro digest plus the full board.
- weekly:     Sunday macro digest.
- macro alert (optional, off by default): one email per macro video/post.

Every email that carries levels is compared with the board as of the previous one, so "changed"
always means "since the last email you got with levels in it".
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

TITLES = {"levels": "点位更新", "premarket": "盘前简报", "close": "盘后简报", "weekly": "宏观周报"}
LEVEL_KINDS = ("levels", "premarket", "close")    # carry the full board
MACRO_KINDS = ("premarket", "close", "weekly")    # carry the macro digest
MACRO_SENT_KEY = "newsletter:macro:last_sent"

MACRO_INSTRUCTIONS = """In `overview`, first organise the macro views by theme (e.g. rates and the Fed, \
inflation and jobs, geopolitics and war, fiscal/liquidity, earnings) with a short paragraph per theme, \
naming which author holds which view, and give the overall risk picture. If level data is present, \
end with a short paragraph on what changed in the levels since the last email and which levels are \
closest to the current price. `focus` lists upcoming events, key risks and key levels to watch; \
`disagreements` lists where the authors disagree."""
LEVEL_INSTRUCTIONS = """Lead the overview with what changed since the last email (new, moved and \
removed levels), then the levels closest to the current price."""
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


def _level_part(cfg: Config, db: DB) -> dict:
    ttl = cfg.get("level_ttl_days")
    active = aggregate.active_levels(db, ttl)
    board = aggregate.build_board(db, prices.get_prices(sorted({l["ticker"] for l in active})), ttl,
                                  cfg.get("cluster_tolerance_pct", 0.6))
    changes = _level_changes(db, board, active)
    near = _near(board)[:20]
    moved = sum(1 for l in changes["changes_list"] if l["change"] == "moved")
    return {
        "active": active, "board": board, "near": near, "snapshot": aggregate.snapshot(active),
        **changes,
        "stats": [("标的", len(board), None), ("点位", len(active), None),
                  ("新增", len(changes["changes_list"]) - moved, "#b7791f"), ("调整", moved, "#6b46c1"),
                  ("移除", len(changes["removed"]), "#8a92a0")],
        "data": {
            "board": _compact_board(board),
            "changes_since_last_email": [
                {"ticker": l["ticker"], "author": l["author"], "type": l["level_type"], "change": l["change"],
                 "price": l["price"], "old_price": l["old_price"], "note": l["note"]}
                for l in changes["changes_list"]] + [
                {"ticker": l["ticker"], "author": l["author"], "type": l["level_type"], "change": "removed",
                 "price": l["price"]} for l in changes["removed"]],
            "levels_near_price": [{"ticker": c["ticker"], "last_price": c["last_price"], "level": c["price"],
                                   "types": c["types"], "authors": c["authors"],
                                   "distance_pct": c["distance_pct"]} for c in near],
        },
    }


def _macro_part(db: DB, kind: str) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7) if kind == "weekly" else _since(db, MACRO_SENT_KEY, timedelta(hours=24))
    macro = aggregate.macro_items(db, since)
    risks = [m["risk_level"] for m in macro if m["risk_level"]]
    return {
        "macro": macro,
        "stats": [("宏观内容", len(macro), None),
                  ("风险偏高", sum(r in ("elevated", "high") for r in risks), "#d64545")],
        "data": {
            "period_since": since.isoformat(),
            "macro": [{"author": m["author"], "source": m["kind"], "title": m["title"],
                       "published": m["published_at"], "risk": m["risk_level"],
                       "summary": m["macro_summary"], "themes": json.loads(m["macro_themes"] or "[]")}
                      for m in macro],
        },
    }


def _change_headline(changes_list: list[dict]) -> str:
    """Subject line for a change alert, written without an LLM call."""
    parts = []
    for l in changes_list[:3]:
        t = LEVEL_TYPES.get(l["level_type"], l["level_type"])
        px = env.filters["px"]
        parts.append(f'{l["ticker"]} {t} {px(l["old_price"])}→{px(l["price"])}' if l["change"] == "moved"
                     else f'{l["ticker"]} 新增{t} {px(l["price"])}')
    more = f" 等 {len(changes_list)} 处" if len(changes_list) > 3 else ""
    return "；".join(parts) + more


def build(cfg: Config, db: DB, llm: LLM, kind: str) -> tuple[str, str, list | None]:
    """Render one email. Returns (subject, html, level snapshot to store once it is sent)."""
    now = datetime.now(timezone.utc)
    lv = _level_part(cfg, db) if kind in LEVEL_KINDS else None
    mc = _macro_part(db, kind) if kind in MACRO_KINDS else None
    board = lv["board"] if lv else []
    macro = mc["macro"] if mc else []

    if kind == "levels":
        # Change alerts go out often; build them without an LLM call so they are instant and free.
        brief = None
        headline = _change_headline(lv["changes_list"]) if lv["changes_list"] else "点位看板"
    else:
        data = {"newsletter": TITLES[kind], **(mc["data"] if mc else {}), **(lv["data"] if lv else {})}
        instructions = MACRO_INSTRUCTIONS if mc else LEVEL_INSTRUCTIONS
        try:
            brief = llm.brief(kind, data, instructions) if (board or macro) else None
        except Exception:
            log.exception("brief generation failed; sending without it")
            brief = None
        headline = brief.headline if brief else ("暂无新内容" if not (board or macro) else now.strftime("%Y-%m-%d"))

    stats = (lv["stats"] if lv else []) + (mc["stats"] if mc and macro else [])
    extra = {k: lv[k] for k in ("first_email", "changes_list", "removed")} if lv else \
        {"first_email": False, "changes_list": [], "removed": []}
    html = env.get_template("newsletter.html").render(
        title=TITLES[kind], kind=kind, headline=headline, brief=brief, board=board,
        near=lv["near"] if lv else [], macro=macro, stats=stats,
        dashboard_url=cfg.get("dashboard_url"), **extra,
        date=now.astimezone(ZoneInfo(cfg.get("timezone", "America/New_York"))).strftime("%Y-%m-%d"),
        local=lambda iso: _local(cfg, iso),
    )
    return f"【{TITLES[kind]}】{headline}", html, (lv["snapshot"] if lv else None)


def send(cfg: Config, db: DB, llm: LLM, kind: str) -> None:
    subject, html, snap = build(cfg, db, llm, kind)
    send_email(subject, html)
    if kind in LEVEL_KINDS:
        db.set_kv(SNAPSHOT_KEY, json.dumps(snap, ensure_ascii=False))
    if kind in ("premarket", "close"):
        db.set_kv(MACRO_SENT_KEY, utcnow())


def send_if_levels_changed(cfg: Config, db: DB, llm: LLM) -> bool:
    """Send a "levels" email when a level was added or moved since the last email with levels.

    Levels that merely expired don't trigger an email on their own; they show up as removed in the
    next one. Returns True if an email was sent.
    """
    active = aggregate.active_levels(db, cfg.get("level_ttl_days"))
    if not active:
        return False
    raw = db.get_kv(SNAPSHOT_KEY)
    if raw is not None:
        changes, _ = aggregate.diff_levels(json.loads(raw), active)
        if not changes:
            return False
    send(cfg, db, llm, "levels")
    return True


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
