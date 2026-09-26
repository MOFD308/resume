"""
python -m market_digest run                  # everything: polling, Discord bot, schedules, dashboard
python -m market_digest poll                 # poll all sources once and extract levels
python -m market_digest send premarket       # send one newsletter now (premarket|midday|close|macro_daily|weekly)
python -m market_digest preview premarket    # write the newsletter HTML to data/ instead of emailing
python -m market_digest resolve-youtube @handle
python -m market_digest check               # verify keys and sources, send a test email
"""
import argparse
import logging
import threading
from datetime import datetime

from .config import ROOT, load_config
from .db import DB
from .llm import LLM

log = logging.getLogger("market_digest")


def _parse_schedule(spec: str) -> dict:
    """'08:45' -> weekdays at 08:45; 'sun 18:00' -> Sundays; 'mon-sun 07:30' -> every day."""
    parts = spec.split()
    day, hm = (parts[0], parts[1]) if len(parts) == 2 else ("mon-fri", parts[0])
    hour, minute = hm.split(":")
    return {"day_of_week": day, "hour": int(hour), "minute": int(minute)}


def run(cfg, db, llm, host: str, port: int) -> None:
    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler

    from . import newsletter, pipeline
    from .dashboard import create_app
    from .sources import discord, windows_notifications

    def process_async():
        threading.Thread(target=pipeline.process_pending, args=(cfg, db, llm), daemon=True).start()

    sched = BackgroundScheduler(timezone=cfg.get("timezone", "America/New_York"))
    sched.add_job(pipeline.run_once, "interval", minutes=cfg.get("poll_minutes", 5),
                  args=(cfg, db, llm), id="poll", max_instances=1, coalesce=True,
                  next_run_time=datetime.now(sched.timezone))
    sched.add_job(pipeline.transcribe_videos, "interval", minutes=3, args=(cfg, db, llm),
                  id="transcribe", max_instances=1, coalesce=True)
    for kind in ("premarket", "midday", "close", "macro_daily", "weekly"):
        spec = cfg.get("schedule", {}).get("weekly_macro" if kind == "weekly" else kind)
        if spec:
            sched.add_job(newsletter.send, "cron", args=(cfg, db, llm, kind), id=kind,
                          misfire_grace_time=1800, **_parse_schedule(spec))
    sched.start()
    log.info("scheduled: %s", ", ".join(f"{j.id} → {j.next_run_time:%a %H:%M}" for j in sched.get_jobs()
                                         if j.next_run_time))

    windows_notifications.start(db, cfg.by_kind("discord"), process_async)
    threading.Thread(target=discord.run_bot, args=(db, cfg.by_kind("discord"), process_async),
                     daemon=True, name="discord").start()

    uvicorn.run(create_app(cfg, db, on_new_item=process_async), host=host, port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(prog="market_digest")
    parser.add_argument("--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--host", default="0.0.0.0")
    p_run.add_argument("--port", type=int, default=8000)
    sub.add_parser("poll")
    for name in ("send", "preview"):
        p = sub.add_parser(name)
        p.add_argument("kind", choices=["premarket", "midday", "close", "macro_daily", "weekly"])
    p_check = sub.add_parser("check")
    p_check.add_argument("--no-email", action="store_true", help="don't send the test email")
    p_yt = sub.add_parser("resolve-youtube")
    p_yt.add_argument("handle")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "apscheduler", "discord", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.cmd == "resolve-youtube":
        from .sources.youtube import resolve_channel_id
        print(resolve_channel_id(args.handle))
        return

    cfg = load_config(args.config)
    db = DB(cfg.db_path)
    llm = LLM(cfg.get("llm", {}).get("model", "claude-opus-5"), cfg.get("llm", {}).get("effort", "medium"))

    if args.cmd == "check":
        from .check import run_checks
        raise SystemExit(0 if run_checks(cfg, send_test_email=not args.no_email) else 1)
    if args.cmd == "run":
        run(cfg, db, llm, args.host, args.port)
    elif args.cmd == "poll":
        from . import pipeline
        pipeline.run_once(cfg, db, llm)
    elif args.cmd == "send":
        from . import newsletter
        newsletter.send(cfg, db, llm, args.kind)
    elif args.cmd == "preview":
        from . import newsletter
        subject, html, _ = newsletter.build(cfg, db, llm, args.kind)
        out = ROOT / "data" / f"preview-{args.kind}.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"{subject}\n→ {out}")


if __name__ == "__main__":
    main()
