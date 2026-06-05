"""
Egyszerű SMTP email küldő riasztásokhoz.

Szükséges környezeti változók productionben:
SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM
Opcionális: SMTP_TLS=true/false
"""
import os
import smtplib
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_email(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    """Email küldése SMTP-n keresztül. Visszatérés: (ok, üzenet)."""
    to_addr = (to_addr or "").strip()
    if not to_addr:
        return False, "Nincs címzett email cím."

    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    from_addr = os.environ.get("SMTP_FROM", "").strip()
    use_tls = os.environ.get("SMTP_TLS", "true").lower() in ("true", "1", "yes")

    if not host or not from_addr:
        return False, "SMTP nincs beállítva. Add meg: SMTP_HOST, SMTP_PORT, SMTP_FROM, szükség esetén SMTP_USERNAME/SMTP_PASSWORD."

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)
        return True, "Email elküldve."
    except Exception as exc:
        return False, f"Email küldési hiba: {exc}"
