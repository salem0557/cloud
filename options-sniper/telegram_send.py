"""Send a message to Salem's Telegram bot.

Two destinations. Alerts go to the main chat; the paper record goes to
TELEGRAM_PAPER_CHAT_ID when it is set, so a month of "closed +40% in 4 minutes"
does not bury the handful of messages he is meant to act on. Unset, both land
in the same place and nothing breaks.

Those two destinations are not always two chats. Salem's are two TOPICS in one
forum group — same group id, different topic — which Telegram addresses with
message_thread_id, not chat_id. Sending without it puts every message in the
group's General topic, which looks like the split working until you open the
group and find both feeds in the wrong place.
"""
import requests

import config as C
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def send_paper(text: str) -> bool:
    """The paper record, to its own chat or topic when one is configured."""
    return send(text, chat_id=C.TELEGRAM_PAPER_CHAT_ID or TELEGRAM_CHAT_ID,
                topic=C.TELEGRAM_PAPER_TOPIC_ID)


def send(text: str, chat_id: str = None, topic: str = None) -> bool:
    # A topic belongs to its chat. Passing the paper topic with the alert chat
    # would either fail or post into whatever thread happens to carry that id
    # there, so the topic only travels with an explicitly given chat_id.
    if chat_id is None:
        chat_id, topic = TELEGRAM_CHAT_ID, C.TELEGRAM_TOPIC_ID
    if not TELEGRAM_TOKEN or not chat_id:
        print("[telegram] missing token/chat id — printing instead:\n", text)
        return False
    payload = {"chat_id": chat_id, "text": text,
               "disable_web_page_preview": True}
    if topic:
        payload["message_thread_id"] = int(topic)
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
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
