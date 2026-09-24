import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def send_email(subject: str, html: str, text: str = "") -> None:
    sender = os.environ["GMAIL_ADDRESS"]
    to = os.environ.get("NEWSLETTER_TO", sender)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(text or "请用支持 HTML 的邮件客户端查看。")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465, timeout=60) as smtp:
        smtp.login(sender, os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        smtp.send_message(msg)
    log.info("sent email: %s", subject)
