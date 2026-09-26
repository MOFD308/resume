"""Discord messages via Windows notifications, on the same PC as the Discord desktop app.

Discord for Windows shows a toast for each new message (when the channel's notifications are set to
"All messages"). This reads those toasts from the Windows notification center with the
UserNotificationListener API and hands them to the same matcher as the Android route. It only
reads what Windows already shows you; it does not touch Discord itself.

Windows only. Needs "Let apps access your notifications" enabled (the `check` command asks for it).
"""
import asyncio
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone

from ..db import DB
from . import discord

log = logging.getLogger(__name__)

POLL_SECONDS = 5
BACKFILL = timedelta(minutes=30)  # on start-up, also pick up toasts from the last 30 minutes


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.ui.notifications.management  # noqa: F401
        return True
    except ImportError:
        return False


async def request_access() -> str:
    """Ask Windows for permission to read notifications. Returns 'allowed', 'denied' or 'unspecified'."""
    from winrt.windows.ui.notifications.management import (
        UserNotificationListener, UserNotificationListenerAccessStatus as Status)
    status = await UserNotificationListener.current.request_access_async()
    return {Status.ALLOWED: "allowed", Status.DENIED: "denied"}.get(status, "unspecified")


def _texts(user_notification) -> tuple[str, str, str]:
    """(app name, title, body) of one toast."""
    try:
        from winrt.windows.ui.notifications import KnownNotificationBindings
        toast_generic = KnownNotificationBindings.toast_generic
    except ImportError:  # tests
        toast_generic = "ToastGeneric"
    try:
        app = user_notification.app_info.display_info.display_name
    except Exception:
        app = ""
    binding = user_notification.notification.visual.get_binding(toast_generic)
    if binding is None:
        return app, "", ""
    parts = [t.text for t in binding.get_text_elements()]
    return app, (parts[0] if parts else ""), "\n".join(parts[1:])


async def poll_once(listener, kinds, seen: set, started: datetime, db: DB, sources, on_new_item=None) -> int:
    """Read the notification center once; store new Discord toasts. Returns how many were stored."""
    stored = 0
    for n in await listener.get_notifications_async(kinds):
        if n.id in seen:
            continue
        seen.add(n.id)
        try:
            created = n.creation_time
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < started - BACKFILL:
                continue
        except Exception:
            pass
        app, title, body = _texts(n)
        if "discord" not in app.lower():
            continue
        result = discord.ingest_notification(
            db, sources, {"package": "com.discord", "title": title, "text": body or title})
        if result["status"] == "stored":
            stored += 1
            log.info("Discord notification stored: %s", title[:60])
            if on_new_item:
                on_new_item()
    return stored


async def _poll_forever(db: DB, sources, on_new_item) -> None:
    from winrt.windows.ui.notifications import NotificationKinds
    from winrt.windows.ui.notifications.management import (
        UserNotificationListener, UserNotificationListenerAccessStatus as Status)

    listener = UserNotificationListener.current
    if listener.get_access_status() != Status.ALLOWED:
        log.warning("Windows 通知读取未授权：运行 windows\\check.bat 授权，或在 设置 → 隐私和安全性 → "
                    "通知 中允许应用访问通知。Discord 电脑通知暂不会被读取。")
        return
    log.info("reading Discord notifications from Windows (%d source(s))", len(sources))
    seen: set[int] = set()
    started = datetime.now(timezone.utc)
    while True:
        try:
            await poll_once(listener, NotificationKinds.TOAST, seen, started, db, sources, on_new_item)
        except Exception:
            log.exception("reading Windows notifications failed")
        await asyncio.sleep(POLL_SECONDS)


def start(db: DB, sources, on_new_item=None) -> None:
    """Start the background reader if this is Windows and Discord sources use `match`."""
    sources = [s for s in sources if s.match]
    if not sources or not available():
        return
    threading.Thread(target=lambda: asyncio.run(_poll_forever(db, sources, on_new_item)),
                     daemon=True, name="windows-notifications").start()
