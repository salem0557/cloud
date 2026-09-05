"""Send a message to Salem's Telegram bot."""
import requests

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def send(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] missing token/chat id — printing instead:\n", text)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
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
