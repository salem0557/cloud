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
import venv_boot

# This one is also run by hand from the Railway console to prove the wiring
# ("python telegram_send.py"), and that console's `python` is the system one
# while the service runs inside /opt/venv. Every other entrypoint boots the
# venv before importing requests; this file imported it at the top and died
# with ModuleNotFoundError, which reads like a broken install.
venv_boot.ensure(["requests"])

import requests

import config as C
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def send_paper(text: str) -> bool:
    """The paper record, to its own chat or topic when one is configured."""
    return send(text, chat_id=C.TELEGRAM_PAPER_CHAT_ID or TELEGRAM_CHAT_ID,
                topic=C.TELEGRAM_PAPER_TOPIC_ID)


def send(text: str, chat_id: str = None, topic: str = None):
    """-> Telegram's message_id (an int, always truthy) or False.

    It used to return a bare bool. The id is what lets a REPLY be matched back
    to the alert it answers, which is the whole mechanism behind Salem saying
    "دخلت" under a message and the system knowing which contract he means.
    Every existing caller tests truthiness, and a message id is never 0.
    """
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
    body = r.json() if r.ok else {}
    if not (r.ok and body.get("ok")):
        print("[telegram] send failed:", r.text[:200])
        return False
    return (body.get("result") or {}).get("message_id") or True


def poll(offset=None, timeout=0):
    """New updates since `offset`. -> (updates, next_offset).

    Long-polling is deliberately off by default: the scheduler already wakes
    every twenty seconds and a blocking call inside that loop would delay the
    scan it exists to run.
    """
    if not TELEGRAM_TOKEN:
        return [], offset
    params = {"timeout": timeout, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params=params, timeout=max(15, timeout + 5))
    except requests.RequestException as e:
        print("[telegram] poll error:", e)
        return [], offset
    body = r.json() if r.ok else {}
    if not (r.ok and body.get("ok")):
        print("[telegram] poll failed:", r.text[:200])
        return [], offset
    updates = body.get("result") or []
    nxt = (updates[-1]["update_id"] + 1) if updates else offset
    return updates, nxt


if __name__ == "__main__":
    where = f"chat {TELEGRAM_CHAT_ID or '(unset)'}"
    if C.TELEGRAM_TOPIC_ID:
        where += f", topic {C.TELEGRAM_TOPIC_ID}"
    print(f"alerts  -> {where}")
    paper = C.TELEGRAM_PAPER_CHAT_ID or TELEGRAM_CHAT_ID
    pw = f"chat {paper or '(unset)'}"
    if C.TELEGRAM_PAPER_TOPIC_ID:
        pw += f", topic {C.TELEGRAM_PAPER_TOPIC_ID}"
    print(f"paper   -> {pw}\n")
    # Both destinations, because the whole point of the split is that they are
    # different places, and one test message cannot show that.
    ok = send("✅ اختبار: قناة التنبيهات")
    ok_paper = send_paper("✅ اختبار: قناة التداول الورقي")
    print("alerts:", "sent" if ok else "NOT sent")
    print("paper :", "sent" if ok_paper else "NOT sent")
    if not (ok and ok_paper):
        print("\nCheck TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, and that the bot\n"
              "is an admin allowed to post in that topic.")
