"""When to answer, and how to split the answer — shared by both Telegram
backends (the BotFather bot and the Telethon userbot).

The rule is the same whichever transport is used: answer when addressed, stay
quiet otherwise, so the agent can sit in a busy group without spamming it.
Keeping it here means the two backends cannot drift apart.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import config, frames, symbols

CAPTION_LIMIT = 1024      # Telegram's cap on a photo caption
MESSAGE_LIMIT = 4000      # under Telegram's 4096-char message cap

DIAG_COMMANDS = {"/diag", ".diag", "/فحص", "فحص"}
HERE_COMMANDS = {"/here", ".here", "/وين", "/id"}
POST_COMMANDS = {"/post", ".post", "/نشر"}
COMMANDS = ({"/help", ".help", "/start", "مساعدة", "/frames", "/ping", ".ping"}
            | DIAG_COMMANDS | HERE_COMMANDS | POST_COMMANDS)
GENERAL_TOPIC = 1      # Telegram reports the General topic as thread id 1/None

HELP = """أنا محلل فني آلي للسوق الأمريكي 📈

كيف تستخدمني:
• أرسل صورة التشارت واكتب معها الرمز والفريم: «حلل TSLA 15 دقيقة»
• أو بدون صورة: «حلل NVDA يومي» / «BTC 4 ساعات»
• أو رد على صورة قديمة بكلمة «حلل»

الفريم هو الأساس: أكتبه أو خلّه ظاهر في الصورة، وإلا سأستخدم اليومي.
الفريمات المدعومة: 1m 5m 15m 30m 1h 2h 4h يومي أسبوعي شهري

ماذا ترجع لك: تشارت جديد بالمؤشرات (EMA 20/50/200، بولنجر، RSI، MACD، فوليوم،
الدعوم والمقاومات، فيبوناتشي، VWAP) + قراءة فنية مع خطة دخول وستوب وأهداف،
وحالة جلسة السوق الأمريكي.

الأوامر: /help | /frames | /ping | /diag (فحص شامل — للمالك)"""

_last_request: dict[int, float] = {}


@dataclass
class Incoming:
    """One message, reduced to what the decision actually depends on."""
    text: str = ""
    chat_id: int = 0
    user_id: int | None = None
    has_photo: bool = False
    replied_has_photo: bool = False
    topic_id: int | None = None   # forum topic (None outside forum groups)
    is_forum: bool = False
    is_private: bool = False
    is_own: bool = False        # sent by the agent's own account (userbot only)
    mentioned: bool = False
    reply_to_me: bool = False


def has_trigger(text: str | None) -> bool:
    if not text:
        return False
    low = frames.normalize(text)
    return any(frames.normalize(trigger) in low for trigger in config.TRIGGERS)


def chat_allowed(chat_id: int) -> bool:
    if chat_id in config.BLOCKED_CHATS:
        return False
    return not config.ALLOWED_CHATS or chat_id in config.ALLOWED_CHATS


def is_command(text: str | None) -> bool:
    return bool(text) and text.strip().lower().split("@")[0] in COMMANDS


def _command(text: str | None) -> str:
    return (text or "").strip().lower().split("@")[0]


def is_diag(text: str | None) -> bool:
    return _command(text) in DIAG_COMMANDS


def is_here(text: str | None) -> bool:
    return _command(text) in HERE_COMMANDS


def is_post(text: str | None) -> bool:
    return _command(text) in POST_COMMANDS


def here_report(msg: Incoming) -> str:
    """Answer to /here: the ids needed to fill in the topic variables."""
    lines = [f"chat_id: `{msg.chat_id}`"]
    if msg.is_forum:
        lines.append(f"topic_id: `{topic_of(msg)}`" + (" (General)" if topic_of(msg) == GENERAL_TOPIC else ""))
    else:
        lines.append("هذه المحادثة ليست قروب توبيكات")
    if config.QA_TOPIC:
        lines.append("قسم الأسئلة المضبوط: " + str(config.QA_TOPIC)
                     + (" ✅ (هذا هو)" if topic_of(msg) == config.QA_TOPIC else " ⚠️ (لست فيه)"))
    else:
        lines.append("قسم الأسئلة غير مضبوط: أضف ANALYST_QA_TOPIC ليجاوب هنا فقط")
    if config.ALERTS_TOPIC:
        lines.append("قسم التوصيات المضبوط: " + str(config.ALERTS_TOPIC))
    return "\n".join(lines)


def diag_allowed(user_id: int | None, is_private: bool) -> bool:
    """The health report names models and settings, so it is owners-only.

    With no owners configured it is allowed in private chats, so a fresh
    install can still be checked before ANALYST_OWNER_IDS is set.
    """
    if config.OWNER_IDS:
        return user_id in config.OWNER_IDS
    return is_private


def cooldown_ok(user_id: int | None) -> bool:
    """One request per user per ANALYST_USER_COOLDOWN seconds (owners exempt)."""
    if not user_id or user_id in config.OWNER_IDS:
        return True
    now = time.time()
    if now - _last_request.get(user_id, 0) < config.USER_COOLDOWN:
        return False
    _last_request[user_id] = now
    return True


def topic_of(msg: Incoming) -> int | None:
    """The topic a message sits in, with General normalised to 1."""
    if not msg.is_forum:
        return None
    return msg.topic_id or GENERAL_TOPIC


def decide(msg: Incoming) -> tuple[bool, str]:
    """(answer?, why) — the single gate both backends go through."""
    if not chat_allowed(msg.chat_id):
        return False, "chat not allowed"
    # In a forum group with a configured Q&A topic, every other topic is
    # somebody else's conversation: stay out of it entirely.
    if config.QA_TOPIC and msg.is_forum and not msg.is_private:
        if topic_of(msg) != config.QA_TOPIC:
            return False, f"wrong topic ({topic_of(msg)})"
    # A userbot runs as its owner's account, so his ordinary chatter arrives
    # here as an outgoing message: act on it only when he asks explicitly.
    if msg.is_own and not (has_trigger(msg.text)
                           or (msg.has_photo and config.ANSWER_ALL_PHOTOS)):
        return False, "own message without trigger"
    if is_command(msg.text):
        return True, "command"

    if msg.is_private and config.DM_ALWAYS_ANSWER:
        if (msg.has_photo or msg.replied_has_photo or has_trigger(msg.text)
                or frames.parse(msg.text) or symbols.resolve(msg.text)):
            return True, "private chat"
        return False, "private but nothing to analyse"

    if has_trigger(msg.text):
        return True, "trigger word"
    if msg.mentioned or msg.reply_to_me:
        return True, "mention/reply"
    # A photo is a request on its own: the caption can be anything, or nothing.
    # Whether it is actually a chart is settled later by the vision read, which
    # drops non-charts without a reply.
    if msg.has_photo and config.ANSWER_ALL_PHOTOS:
        return True, "photo"
    return False, "not addressed"


def addressed_explicitly(msg: Incoming) -> bool:
    """Did the user clearly ask *the agent*?

    This decides what happens when the picture is not a chart: an explicit ask
    deserves an answer even if the vision read disagrees, while a photo dropped
    into a group does not deserve a reply at all.
    """
    return bool(has_trigger(msg.text) or msg.mentioned or msg.reply_to_me
                or msg.is_private)


def chunks(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on line breaks so a section never breaks mid-sentence."""
    if len(text) <= limit:
        return [text]
    out, current = [], ""
    for block in text.split("\n"):
        if len(current) + len(block) + 1 > limit:
            out.append(current.rstrip())
            current = ""
        current += block + "\n"
    if current.strip():
        out.append(current.rstrip())
    return out


def command_reply(text: str) -> str | None:
    """Canned answer for a command, or None if it is not one."""
    command = text.strip().lower().split("@")[0]
    if command in ("/help", ".help", "/start", "مساعدة"):
        return HELP
    if command == "/frames":
        return "الفريمات المدعومة: " + " | ".join(frames.all_keys())
    if command in ("/ping", ".ping"):
        return "شغّال ✅"
    if command in HERE_COMMANDS or command in POST_COMMANDS:
        return None      # both need the message itself, handled by the backend
    return None
