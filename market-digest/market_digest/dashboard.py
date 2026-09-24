"""Web dashboard (always up to date) + the endpoint the Android phone forwards Discord notifications to."""
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import aggregate, prices
from .config import Config
from .db import DB
from .sources import discord

security = HTTPBasic(auto_error=False)


def create_app(cfg: Config, db: DB, on_new_item=None) -> FastAPI:
    app = FastAPI(title="Market Digest", docs_url=None, redoc_url=None)
    page = (Path(__file__).parent / "templates" / "dashboard.html").read_text()

    def auth(creds: HTTPBasicCredentials | None = Depends(security)):
        password = os.environ.get("DASHBOARD_PASSWORD")
        if not password:
            return
        user = os.environ.get("DASHBOARD_USER", "me")
        if not creds or not (secrets.compare_digest(creds.username, user)
                             and secrets.compare_digest(creds.password, password)):
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index():
        return page

    @app.get("/api/board", dependencies=[Depends(auth)])
    def board():
        ttl = cfg.get("level_ttl_days")
        tickers = sorted({l["ticker"] for l in aggregate.active_levels(db, ttl)})
        rows = aggregate.build_board(db, prices.get_prices(tickers), ttl,
                                     cfg.get("cluster_tolerance_pct", 0.6))
        return {"updated": datetime.now(timezone.utc).isoformat(), "rows": rows}

    @app.get("/api/feed", dependencies=[Depends(auth)])
    def feed(limit: int = 40):
        items = db.query(
            "SELECT id, kind, author, title, url, published_at, status, summary, is_macro, risk_level "
            "FROM items WHERE status IN ('done','waiting') ORDER BY published_at DESC LIMIT ?", (limit,))
        counts = {r["item_id"]: r["n"] for r in db.query(
            "SELECT item_id, COUNT(*) n FROM levels GROUP BY item_id")}
        for it in items:
            it["levels"] = counts.get(it["id"], 0)
        return items

    @app.get("/api/macro", dependencies=[Depends(auth)])
    def macro(days: int = 7):
        rows = aggregate.macro_items(db, datetime.now(timezone.utc) - timedelta(days=days))
        for r in rows:
            r["macro_themes"] = json.loads(r["macro_themes"] or "[]")
        return rows

    @app.get("/api/sources", dependencies=[Depends(auth)])
    def sources():
        stats = {r["author"]: r for r in aggregate.source_stats(db)}
        return [{"name": s.name, "kind": s.kind, "category": s.category,
                 **{k: stats.get(s.name, {}).get(k) for k in ("n", "levels", "macro", "last", "top_tickers")}}
                for s in cfg.sources]

    @app.post("/ingest/android")
    async def ingest_android(request: Request):
        token = os.environ.get("INGEST_TOKEN")
        given = request.query_params.get("token") or request.headers.get("x-token", "")
        if not token or not secrets.compare_digest(given, token):
            raise HTTPException(403)
        if "json" in request.headers.get("content-type", ""):
            payload = await request.json()
        else:
            payload = dict(await request.form())
        result = discord.ingest_notification(db, cfg.by_kind("discord"), payload)
        if result["status"] == "stored" and on_new_item:
            on_new_item()
        return result

    return app
