"""Send a message to Salem's Telegram bot.

Two destinations. Alerts go to the main chat; the paper record goes to
TELEGRAM_PAPER_CHAT_ID when it is set, so a month of "closed +40% in 4 minutes"
does not bury the handful of messages he is meant to act on. Unset, both land
in the same place and nothing breaks.
"""
import requests

import config as C
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def send_paper(text: str) -> bool:
    """The paper record, to its own chat when one is configured."""
    return send(text, chat_id=C.TELEGRAM_PAPER_CHAT_ID or TELEGRAM_CHAT_ID)


def send(text: str, chat_id: str = None) -> bool:
    chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not chat_id:
        print("[telegram] missing token/chat id — printing instead:\n", text)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "disable_web_page_preview": True},
            timeout=15,
        )
    except requests.RequestException as e:
        print("[telegram] network error:", e)
        return False
    ok = r.ok and r.json().get("ok", False)
    if not ok:
        print("[telegram] send failed:", r.text[:200])
    return ok


if __name__ == "__main__":
    ok = send("✅ اختبار: بوت التنبيهات يعمل")
    print("sent" if ok else "NOT sent — check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env")
