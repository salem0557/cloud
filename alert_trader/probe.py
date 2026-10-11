"""Practical check that the source channel's posts actually reach us.

    python -m alert_trader probe --minutes 15

Bot API part: confirms the bot token, that the channel is visible, that the
bot is an administrator, that no webhook steals the updates, then long-polls
for channel posts and prints each one with who published it. Stop the running
bot first: two getUpdates consumers on one token conflict (HTTP 409).

User-account part (if TG_* variables are set): reads the channel's latest
posts as a subscriber and listens for the same window, so both transports are
compared on the same real posts. Nothing is traded during a probe.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

import requests

from .channel import from_telethon, userbot_client
from .storage import Store, iso, now_utc

API = "https://api.telegram.org/bot{token}/{method}"


def _call(token, method, **params):
    r = requests.post(API.format(token=token, method=method), json=params, timeout=70)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description')}")
    return data["result"]


def probe_botapi(env, minutes: float, out: list[str]) -> dict:
    res = {"transport": "botapi", "posts": [], "ok": False}
    if not env.bot_token:
        out.append("✗ ALERT_BOT_TOKEN غير معيّن")
        return res
    me = _call(env.bot_token, "getMe")
    out.append(f"✓ البوت: @{me['username']}")
    hook = _call(env.bot_token, "getWebhookInfo")
    if hook.get("url"):
        out.append("✗ يوجد Webhook مفعّل — يمنع الاستقبال بالـ polling")
        return res
    try:
        chat = _call(env.bot_token, "getChat", chat_id=env.channel_id)
        out.append(f"✓ القناة: {chat.get('title')} ({chat.get('type')})")
        member = _call(env.bot_token, "getChatMember", chat_id=env.channel_id, user_id=me["id"])
        res["bot_status"] = member.get("status")
        if member.get("status") != "administrator":
            out.append(f"✗ البوت ليس مشرفًا في القناة (الحالة: {member.get('status')}) — أضفه كمشرف")
            return res
        out.append("✓ البوت مشرف في القناة")
    except RuntimeError as e:
        out.append(f"✗ لا يمكن الوصول للقناة: {e}")
        return res
    out.append(f"… أستمع {minutes:g} دقيقة لمنشورات القناة عبر Bot API")
    offset, deadline = None, time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        params = {"timeout": 50, "allowed_updates": ["channel_post", "edited_channel_post"]}
        if offset:
            params["offset"] = offset
        for u in _call(env.bot_token, "getUpdates", **params):
            offset = u["update_id"] + 1
            m = u.get("channel_post") or u.get("edited_channel_post")
            if not m or m["chat"]["id"] != env.channel_id:
                continue
            row = {"message_id": m["message_id"], "edited": "edited_channel_post" in u,
                   "author_signature": m.get("author_signature"),
                   "via_bot": (m.get("via_bot") or {}).get("username"),
                   "sender_chat": (m.get("sender_chat") or {}).get("id"),
                   "text": (m.get("text") or m.get("caption") or "")[:80]}
            res["posts"].append(row)
            out.append(f"  ← {json.dumps(row, ensure_ascii=False)}")
    res["ok"] = bool(res["posts"])
    return res


async def _probe_userbot(env, minutes: float, out: list[str]) -> dict:
    from telethon import events
    res = {"transport": "userbot", "posts": [], "history": [], "ok": False}
    client = userbot_client(env)
    await client.start()
    me = await client.get_me()
    out.append(f"✓ حساب المستخدم: {me.first_name} (@{me.username})")
    entity = await client.get_entity(env.channel_id)
    out.append(f"✓ القناة مرئية للحساب: {getattr(entity, 'title', env.channel_id)}")
    for m in await client.get_messages(entity, limit=5):
        raw = from_telethon(m, env.channel_id)
        res["history"].append({"message_id": raw.message_id, "author": raw.author_signature,
                               "via_bot": raw.via_bot, "text": raw.text[:80]})
    out.append(f"✓ آخر {len(res['history'])} منشورات مقروءة من سجل القناة")

    async def on(event):
        raw = from_telethon(event.message, event.chat_id)
        row = {"message_id": raw.message_id, "edited": raw.edit_date is not None,
               "author": raw.author_signature, "via_bot": raw.via_bot, "text": raw.text[:80]}
        res["posts"].append(row)
        out.append(f"  ← {json.dumps(row, ensure_ascii=False)}")

    client.add_event_handler(on, events.NewMessage(chats=env.channel_id))
    client.add_event_handler(on, events.MessageEdited(chats=env.channel_id))
    out.append(f"… أستمع {minutes:g} دقيقة عبر حساب المستخدم")
    await asyncio.sleep(minutes * 60)
    await client.disconnect()
    res["ok"] = bool(res["posts"])
    return res


def run(env, minutes: float, which: str = "both") -> str:
    out: list[str] = [f"فحص استقبال القناة {env.channel_id} — {iso(now_utc())}"]
    results = []
    if env.channel_id is None:
        return "✗ SOURCE_CHANNEL_ID غير معيّن"
    if which in ("both", "botapi"):
        results.append(probe_botapi(env, minutes, out))
    if which in ("both", "userbot") and env.tg_session:
        results.append(asyncio.run(_probe_userbot(env, minutes, out)))
    out.append("")
    for r in results:
        verdict = (f"✓ استُقبل {len(r['posts'])} منشور" if r["ok"]
                   else "✗ لم يُستقبل أي منشور خلال المدة (لا يُعتبر الاستقبال مؤكدًا)")
        out.append(f"النتيجة [{r['transport']}]: {verdict}")
    out.append("ملاحظة: النتيجة لا تكون حاسمة إلا إذا نشر بوت المصدر تنبيهًا واحدًا على الأقل أثناء الفحص.")
    store = Store(env.data_dir / "alert_trader.db")
    store.set("probe_result", {"at": iso(now_utc()), "results": results})
    store.event("probe", "فحص استقبال القناة", data=results)
    return "\n".join(out)
