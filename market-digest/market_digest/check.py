"""`python -m market_digest check`: verify keys and sources one by one, in plain language."""
import os
import smtplib

import httpx

from .config import Config

OK, FAIL, SKIP = "✅", "❌", "➖"


def _line(status: str, name: str, detail: str = "") -> bool:
    print(f"{status} {name}" + (f"：{detail}" if detail else ""))
    return status != FAIL


def run_checks(cfg: Config, send_test_email: bool = True) -> bool:
    ok = True
    print("检查配置和连接……\n")

    # Claude API: retrieving the model's metadata validates the key without spending tokens.
    model = cfg.get("llm", {}).get("model", "claude-opus-5")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        ok &= _line(FAIL, "Claude API", ".env 里的 ANTHROPIC_API_KEY 还没填")
    else:
        import anthropic
        try:
            anthropic.Anthropic().models.retrieve(model)
            _line(OK, "Claude API", f"key 有效，模型 {model} 可用")
        except anthropic.AuthenticationError:
            ok &= _line(FAIL, "Claude API", "key 无效，请到控制台重新复制")
        except anthropic.NotFoundError:
            ok &= _line(FAIL, "Claude API", f"找不到模型 {model}，检查 config.yaml 的 llm.model")
        except anthropic.APIError as e:
            ok &= _line(FAIL, "Claude API", str(e)[:150])

    # Gmail
    addr, pw = os.environ.get("GMAIL_ADDRESS", ""), os.environ.get("GMAIL_APP_PASSWORD", "")
    if not pw or addr in ("", "you@gmail.com"):
        ok &= _line(FAIL, "Gmail", ".env 里的 GMAIL_ADDRESS / GMAIL_APP_PASSWORD 还没填")
    else:
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
                smtp.login(addr, pw.replace(" ", ""))
            _line(OK, "Gmail", f"可以用 {addr} 发邮件")
            if send_test_email:
                from .mailer import send_email
                send_email("【Market Digest】安装成功 ✅",
                           "<p style='font-family:sans-serif'>这是一封测试邮件：程序可以正常给你发邮件了。</p>")
                _line(OK, "测试邮件", f"已发送到 {os.environ.get('NEWSLETTER_TO', addr)}，请查收（也看看垃圾邮件）")
        except smtplib.SMTPAuthenticationError:
            ok &= _line(FAIL, "Gmail", "登录失败：要用“应用专用密码”（16 位），不是 Gmail 登录密码")
        except Exception as e:
            ok &= _line(FAIL, "Gmail", str(e)[:150])

    # X
    x_sources = cfg.by_kind("x")
    token = os.environ.get("X_BEARER_TOKEN")
    if not x_sources:
        _line(SKIP, "X", "没有配置 X 账号")
    elif not token:
        _line(SKIP, "X", "没填 X_BEARER_TOKEN，将改为读取 X 通知邮件")
    else:
        handle = x_sources[0].handle.lstrip("@")
        try:
            r = httpx.get(f"https://api.x.com/2/users/by/username/{handle}",
                          headers={"Authorization": f"Bearer {token}"}, timeout=20)
            if r.status_code == 200:
                _line(OK, "X API", f"token 有效，找到 @{handle}")
            elif r.status_code in (401, 403):
                ok &= _line(FAIL, "X API", f"token 无效或账户没有额度（HTTP {r.status_code}）")
            else:
                ok &= _line(FAIL, "X API", f"HTTP {r.status_code}: {r.text[:120]}")
        except httpx.HTTPError as e:
            ok &= _line(FAIL, "X API", str(e)[:150])

    # YouTube: discovery + one subtitle download
    from .sources import youtube
    for src in cfg.by_kind("youtube"):
        try:
            videos = youtube._from_feed(src.channel_id) or youtube._from_channel_page(src.channel_id)
        except httpx.HTTPError as e:
            ok &= _line(FAIL, f"YouTube {src.name}", f"打不开频道：{e}")
            continue
        if not videos:
            ok &= _line(FAIL, f"YouTube {src.name}", "没读到视频列表，检查 channel_id")
            continue
        vid = videos[0]["id"]
        try:
            text = youtube.fetch_transcript(vid, (cfg.get("youtube_transcribe") or {}).get("language"))
            if text:
                _line(OK, f"YouTube {src.name}", f"能读到视频和字幕（最新视频字幕 {len(text)} 字）")
            else:
                _line(OK, f"YouTube {src.name}", "能读到视频；最新视频暂无字幕，会自动用语音识别")
        except youtube.SubtitlesDisabled:
            _line(OK, f"YouTube {src.name}", "能读到视频；频道关闭了字幕，会自动用语音识别")
        except Exception as e:
            msg = " ".join(str(e).split())
            if "blocking requests from your IP" in msg or "IpBlocked" in type(e).__name__:
                ok &= _line(FAIL, f"YouTube {src.name}", "YouTube 拦截了这个网络的字幕请求（云服务器 IP？见 README 代理设置）")
            else:
                ok &= _line(FAIL, f"YouTube {src.name}", msg[:150])

    # Discord via Windows notifications
    from .sources import windows_notifications
    discord_srcs = [s for s in cfg.by_kind("discord") if s.match]
    if not discord_srcs:
        _line(SKIP, "Discord", "没有配置 Discord 博主")
    elif not windows_notifications.available():
        _line(SKIP, "Discord 电脑通知", "只在 Windows 上可用（或组件未安装，重新运行 install.bat）")
    else:
        import asyncio
        try:
            status = asyncio.run(windows_notifications.request_access())
        except Exception as e:
            status = f"error: {e}"
        if status == "allowed":
            _line(OK, "Discord 电脑通知", "已获准读取 Windows 通知。请确认 Discord 电脑版已登录、该频道通知设为「所有消息」")
        else:
            ok &= _line(FAIL, "Discord 电脑通知",
                        f"未获准读取通知（{status}）。到 设置 → 隐私和安全性 → 通知，打开「允许应用访问通知」后重试")

    try:
        import faster_whisper  # noqa: F401
        import yt_dlp  # noqa: F401
        _line(OK, "语音识别组件", "已安装（第一次转写时会自动下载 Whisper 模型，约 500MB）")
    except ImportError:
        ok &= _line(FAIL, "语音识别组件", "没装好，重新运行 install.bat")

    print("\n" + ("全部通过，可以启动了！" if ok else "有 ❌ 的项目需要处理，改好后再运行一次检查。"))
    return ok
