"""The analyst persona, the payload it is allowed to see, and a template-only
fallback for when Groq is unreachable.

The model is explicitly not the decision maker: direction, entry, stop and
targets arrive already computed in `verdict`, and the prompt forbids changing
them or inventing any number. That is what makes the agent's confidence
defensible instead of decorative.
"""
from __future__ import annotations

import json

SYSTEM = """أنت محلل فني محترف بخبرة عشرين سنة في أسواق الأسهم الأمريكية والسوق السعودي والعملات والسلع.
تتكلم بلغة قاطعة وواضحة بلا مواربة، وتلتزم بما يلي بحذافيره:

1) كل رقم تذكره يجب أن يكون موجوداً حرفياً في بيانات JSON المرفقة. يمنع منعاً تاماً اختراع أي سعر أو نسبة أو
   مستوى أو خبر. إن لم يكن الرقم في البيانات فلا تذكره.
2) الحكم النهائي (الاتجاه، الدخول، الستوب، الأهداف) محسوب مسبقاً في القسم verdict. مهمتك أن تشرحه وتدافع عنه
   بلغة المحلل، لا أن تغيّره ولا أن تقترح غيره.
3) التحليل يكون على الفريم المطلوب فقط (requested_frame). الفريم الأعلى يُستخدم للسياق والتحذير من التعارض فقط.
4) لا تُكرر القوائم كما هي؛ اربط الإشارات في قراءة واحدة مترابطة: أين السعر، من يسيطر، ما الدليل، وماذا بعد.
5) إن كان في البيانات تعارض (conflicts) أو ضعف سيولة أو نتائج مالية قريبة، اذكره صراحة بسطر واحد — الثقة لا تعني
   إخفاء المخاطر.
6) الستوب هو أداة حماية رأس المال: لا توسّعه ولا تتجاهله. ربح صغير مع خسارة أصغر أفضل من هدف بعيد.
7) اكتب بالعربية الفصحى المبسطة، أرقام إنجليزية، بلا رموز ماركداون معقّدة (لا جداول ولا عناوين #)، ومناسب
   لقراءته في تيليجرام.
8) لا تكتب إخلاء مسؤولية طويلاً؛ سطر واحد في النهاية يكفي.
8-أ) انظر verdict.target_eta_bars مقابل verdict.horizon_bars: إن كان الهدف الأول يحتاج شموعاً
   أكثر من مدة السؤال، قل ذلك صراحة قبل الخطة، واذكر verdict.horizon_target كهدف واقعي ضمن
   المدة. لا تعد بهدف لا يبلغه السعر في الوقت المسؤول عنه.
8-ب) إن سأل المستخدم "كم يصل السعر بعد كذا؟" فأجب بالنطاق من verdict.expected_range مع مدة
   technicals.horizon.label، واذكر أنه نطاق احتمالي من ATR لا رقم مؤكد. لا تعطِ رقماً واحداً
   قاطعاً، ولا تخترع نطاقاً غير الموجود في البيانات. وإن وُجد technicals.horizon.session_note
   فاذكره: النطاق لا يشمل ما بعد إغلاق السوق ولا الفتحة السعرية في الافتتاح.
8-ج) المؤشرات كلها محسوبة على شموع مغلقة. إن وُجد technicals.last_closed_price فالسعر المعروض
   لحظي داخل شمعة لم تُغلق: اذكر الاثنين ولا تبنِ نموذج شمعة على الشمعة الجارية.
9) تخصصك السوق الأمريكي والكريبتو. انظر إلى technicals.session: إن كان السوق مغلقاً أو في
   البري ماركت/الأفتر أورز، أو كانت آخر شمعة متأخرة (stale_note)، فقل ذلك بسطر واحد قبل الخطة —
   الخطة تُنفَّذ عند الافتتاح وليس الآن. أما الكريبتو (asset_class = crypto) فسوقه مفتوح 24 ساعة:
   لا تتحدث عن افتتاح أو إغلاق، وراعِ أن السبريد والسيولة يضعفان في عطلة نهاية الأسبوع.

اكتب الإجابة بهذا الهيكل بالضبط وبهذا الترتيب:

🎯 الخلاصة: <الاتجاه> — ثقة <conviction>% (<conviction_ar>)
📐 الفريم: <الفريم المطلوب> | السعر: <price> | آخر شمعة: <last_bar_time>
🔍 القراءة الفنية:
• ثلاث إلى خمس نقاط، كل نقطة بدليل رقمي من البيانات (متوسطات، RSI، MACD، ADX، فوليوم، هيكل القمم والقيعان).
🧱 المستويات التي تهم:
• الدعوم والمقاومات الأقرب مع عدد اللمسات، وأي مستوى فيبوناتشي أو VWAP له أثر.
🔮 النطاق المتوقع خلال <technicals.horizon.label>: <verdict.expected_range>
   (مشتق من مدى التذبذب الفعلي ATR على هذا الفريم، وليس تنبؤاً بسعر واحد)
🧭 الخطة:
• الدخول: <entry> (<entry_note>)
• الستوب: <stop> (مخاطرة <risk_pct>%)
• الأهداف: <targets> | العائد/المخاطرة: <rr>
⚠️ ما يلغي السيناريو: <invalidation> + أي تعارض أو خطر حدث قريب.
📰 الأخبار والمزاج: سطر أو سطران من العناوين والمزاج الاجتماعي إن وُجدت، وإلا اكتب "لا يوجد جديد مؤثر".

إن كان الاتجاه عرضياً وبلا خطة، قل ذلك بصراحة واذكر شرط الدخول الذي يجب انتظاره بدل اختراع صفقة."""

QUESTION_HINT = """سؤال المستخدم المحدد: "{question}"
أجب عليه في أول سطرين بشكل مباشر، ثم أكمل الهيكل المطلوب."""


SYSTEM_SIMPLE = """أنت محلل فني محترف يكتب لمتداول مشغول في قروب تيليجرام.
مهمتك: قرار واضح في أقل عدد كلمات. الأرقام كلها من JSON المرفق — يمنع اختراع أي رقم،
والحكم في verdict محسوب مسبقاً فلا تغيّره.

اكتب بهذا الشكل بالضبط، بلا أي إضافة ولا مقدمة ولا خاتمة، وبحد أقصى 12 سطراً:

<🔴 أو 🟢 أو ⚪> <الاتجاه> — ثقة <conviction>%
<الرمز> · <الفريم> · <السعر>

<سطر القرار: ✅ إشارة قوية | ⚠️ إشارة متوسطة، حجم صغير | ⛔ الأفضل الانتظار | ⏸️ عرضي بلا صفقة>
<إن وُجدت conflicts: نقطة أو نقطتان بأقصر عبارة ممكنة، كل واحدة في سطر يبدأ بـ •>

📋 الخطة: دخول <entry> · ستوب <stop> (<risk_pct>%) · هدف <أول هدف> (<reward_pct>%)
🔮 خلال <horizon.label>: <expected_range من — إلى>
📊 لماذا: أربع إشارات بأقصر صياغة، مفصولة بـ ·  (مثال: متوسطات هابطة · ADX 44 · RSI 39 · فوليوم ضعيف)
<📰 سطر واحد فقط إن وُجد خبر مؤثر، وإلا احذف السطر>

قواعد صارمة:
- لا تشرح المؤشرات ولا تذكر أسماء الحقول الإنجليزية (EMA fast، DI-minus، lower_low…).
- الأسعار بفواصل الآلاف وبلا أصفار زائدة.
- لا تكتب فقرات، فقط الأسطر أعلاه.
- إن كان الاتجاه عرضياً فاكتب شرط الدخول الذي ننتظره بدل خطة وهمية.
- لا تنويه ولا إخلاء مسؤولية؛ يُضاف آلياً.
- سطر الأخبار نقلٌ للسياق فقط ولا يدخل في الحكم: اذكر العنوان ومصدره، ولا تبنِ عليه توقعاً
  ولا تناقض به الاتجاه المحسوب. وإن كان العنوان عن رأي طويل المدى بينما الفريم قصير،
  فاذكر ذلك بكلمتين (مثال: "رأي طويل المدى")."""


def build_payload(*, symbol: str, meta: dict, requested_frame: str, used_frame: str,
                  facts: dict, context_facts: dict | None, verdict: dict,
                  news: dict | None, chart_read: dict | None,
                  fallback_note: str | None) -> str:
    """The single JSON blob the model may quote from — nothing else."""
    payload = {
        "symbol": symbol,
        "asset": {
            "name": meta.get("name") or meta.get("long_name"),
            "exchange": meta.get("exchange"), "currency": meta.get("currency"),
            "sector": meta.get("sector"), "industry": meta.get("industry"),
            "market_cap": meta.get("market_cap"), "pe": meta.get("pe"),
            "forward_pe": meta.get("forward_pe"), "beta": meta.get("beta"),
            "analyst_target": meta.get("target_mean"),
            "analyst_recommendation": meta.get("recommendation"),
            "short_float_pct": meta.get("shares_short_pct"),
            "quote_type": meta.get("quote_type"),
        },
        "requested_frame": requested_frame,
        "analysed_frame": used_frame,
        "frame_note": fallback_note,
        "data_as_of": meta.get("fetched_at"),
        "technicals": facts,
        "higher_timeframe": context_facts,
        "verdict": verdict,
        "news": news or {},
        "screenshot": chart_read or {},
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


DETAIL_WORDS = ("تفصيلي", "بالتفصيل", "تفاصيل", "مفصل", "مطول", "detail", "full")


def wants_detail(question: str | None) -> bool:
    """The long form on request, whatever the configured default is."""
    if not question:
        return False
    low = question.lower()
    return any(word in low for word in DETAIL_WORDS)


def build_messages(payload: str, question: str | None,
                   style: str | None = None) -> list[dict]:
    from . import config

    style = style or config.ANSWER_STYLE
    system = SYSTEM if (style == "full" or wants_detail(question)) else SYSTEM_SIMPLE
    user = ["هذه بيانات السهم/الأصل بالكامل. حلّلها والتزم بالهيكل والقواعد:", payload]
    if question:
        user.insert(0, QUESTION_HINT.format(question=question.strip()[:400]))
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user)},
    ]


# --- template fallback ------------------------------------------------------
def _fmt(value, price: float | None = None) -> str:
    """A price a human reads: thousands separated, no trailing zeros."""
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    reference = abs(price if price is not None else number)
    digits = 4 if reference < 10 else 2 if reference < 1000 else 0
    return f"{number:,.{digits}f}"


def _action_line(verdict: dict) -> str:
    """The one line that says what to do, before any number."""
    if verdict.get("side") == "none" or not verdict.get("entry"):
        return "⏸️ عرضي — لا صفقة واضحة، انتظر كسر المستوى بإغلاق"
    conflicts = verdict.get("conflicts") or []
    weak_rr = (verdict.get("rr") or 0) < 1
    if len(conflicts) >= 2 or weak_rr:
        return "⛔ الأفضل الانتظار"
    if verdict.get("conviction", 0) >= 65 and not conflicts:
        return "✅ إشارة قوية"
    return "⚠️ إشارة متوسطة — بحجم صغير"


def simple_text(*, symbol: str, frame_label: str, facts: dict, verdict: dict,
                news: dict | None = None, note: str | None = None) -> str:
    """The short answer: a decision, a plan, a projection, and why — no essay.

    Kept under Telegram's caption limit on purpose, so the whole read rides
    under the chart as one message instead of a picture plus a wall of text.
    """
    price = facts["price"]
    arrow = {"long": "🟢", "short": "🔴"}.get(verdict.get("side"), "⚪")
    lines = [
        f"{arrow} {verdict['direction']} — ثقة {verdict['conviction']}%",
        f"{symbol} · {frame_label} · {_fmt(price, price)}",
        "",
        _action_line(verdict),
    ]
    for conflict in (verdict.get("conflicts") or [])[:2]:
        short = conflict.split("—")[0].split(":")[0].strip()
        if len(short) > 58:                       # one line, not a paragraph
            short = short[:58].rsplit(" ", 1)[0] + "…"
        lines.append(f"• {short}")

    if verdict.get("entry"):
        target = (verdict.get("targets") or [None])[0]
        plan = (f"📋 دخول {_fmt(verdict['entry'], price)} · "
                f"ستوب {_fmt(verdict['stop'], price)} ({verdict['risk_pct']}%)")
        if target is not None:
            plan += f" · هدف {_fmt(target, price)} ({verdict.get('reward_pct')}%)"
        lines += ["", plan]

    horizon = facts.get("horizon") or {}
    expected = verdict.get("expected_range")
    if expected and horizon.get("label"):
        lines.append(f"🔮 خلال {horizon['label']}: {_fmt(expected[0], price)} – "
                     f"{_fmt(expected[1], price)}")

    trend = facts["trend"]
    mom = facts["momentum"]
    why = [
        "متوسطات " + {"bullish": "صاعدة", "bearish": "هابطة"}.get(trend["ema_stack"], "متشابكة"),
        f"ADX {trend['adx']:.0f}" if trend.get("adx") else None,
        f"RSI {mom['rsi']:.0f}",
        "فوليوم ضعيف" if facts["volume"].get("dry") else
        (f"فوليوم {facts['volume']['relative']}×" if facts["volume"].get("relative") else None),
    ]
    lines.append("📊 " + " · ".join(w for w in why if w))

    headlines = (news or {}).get("headlines") or []
    if headlines:
        top = headlines[0]
        source = " — ".join(part for part in (top.get("publisher"), top.get("age_label"))
                            if part)
        lines.append("📰 " + top["title"][:100] + (f" ({source})" if source else ""))
    if note:
        lines.append("ℹ️ " + note)
    session_state = facts.get("session") or {}
    if session_state.get("stale_note"):
        lines.append("⚠️ البيانات ليست لحظية — تأكد من السعر")
    return "\n".join(lines)



def fallback_text(*, symbol: str, frame_label: str, facts: dict, verdict: dict,
                  news: dict | None = None, note: str | None = None) -> str:
    """A full read built from the numbers alone, used when Groq is unavailable.

    Same numbers, plainer language — the user still gets a usable answer.
    """
    trend = facts["trend"]
    mom = facts["momentum"]
    vola = facts["volatility"]
    vol = facts["volume"]
    lv = facts["levels"]
    lines = [
        f"🎯 الخلاصة: {verdict['direction']} — ثقة {verdict['conviction']}% ({verdict['conviction_ar']})",
        f"📐 {symbol} | الفريم: {frame_label} | السعر: {facts['price']}"
        + (f" (آخر إغلاق {facts['last_closed_price']})" if facts.get("last_closed_price") else "")
        + f" | آخر شمعة مغلقة: {facts['last_bar_time']}",
        "",
        "🔍 القراءة الفنية:",
        f"• ترتيب المتوسطات: {trend['ema_stack']} (20: {trend['ema_fast']} / 50: {trend['ema_mid']} / 200: {trend['ema_slow']})",
        f"• {trend['structure_ar']} | ADX {trend.get('adx')}",
        f"• RSI {mom['rsi']} ({mom['rsi_state']}) | MACD hist {mom['macd_hist']}"
        + (f" | دايفرجنس {mom['rsi_divergence']}" if mom.get("rsi_divergence") else ""),
        f"• الفوليوم {vol.get('relative')}× المعدل و OBV {vol.get('obv_direction')}",
        f"• ATR {vola['atr']} ({vola['atr_pct']}%) — مدى الحركة الطبيعي للشمعة",
    ]
    if facts.get("patterns_ar"):
        lines.append("• نماذج الشموع: " + "، ".join(facts["patterns_ar"][:3]))
    lines += [
        "",
        "🧱 المستويات التي تهم:",
        "• أقرب دعم: " + (str(lv.get("nearest_support")) if lv.get("nearest_support")
                          else "لا دعم قريب فوق القاع الأخير"),
        "• أقرب مقاومة: " + (str(lv.get("nearest_resistance")) if lv.get("nearest_resistance")
                            else "لا مقاومة قريبة — السعر في منطقة قمم جديدة"),
        f"• قمة 52 أسبوع {lv.get('high_52w')} وقاعها {lv.get('low_52w')}",
    ]
    if facts.get("vwap"):
        lines.append(f"• VWAP {facts['vwap']} والسعر {facts.get('price_vs_vwap_pct')}% عنه")
    horizon = facts.get("horizon") or {}
    expected = verdict.get("expected_range")
    if expected and horizon.get("label"):
        lines += ["", f"🔮 النطاق المتوقع خلال {horizon['label']} "
                      f"({horizon.get('bars_text', horizon.get('bars'))} شمعة): "
                      f"{expected[0]} – {expected[1]}",
                  "• مشتق من مدى التذبذب الفعلي (ATR)، احتمالي وليس رقماً مؤكداً."]
        if horizon.get("session_note"):
            lines.append("• " + horizon["session_note"])
        eta = verdict.get("target_eta_bars")
        if eta and verdict.get("horizon_bars") and eta > verdict["horizon_bars"]:
            lines.append(f"• الهدف الأول يحتاج ~{eta} شمعة على الأقل، أي أبعد من هذه المدة — "
                         f"الواقعي خلالها {verdict.get('horizon_target')}")
    lines.append("")
    if verdict.get("entry"):
        lines += [
            "🧭 الخطة:",
            f"• الدخول: {verdict['entry']} ({verdict['entry_note']})",
            f"• الستوب: {verdict['stop']} (مخاطرة {verdict['risk_pct']}%)",
            f"• الأهداف: {'، '.join(str(t) for t in verdict['targets'])} | العائد/المخاطرة {verdict['rr']}",
            f"• نسبة النجاح المطلوبة للتعادل: {verdict['breakeven_rate']}%",
        ]
    else:
        lines += ["🧭 الخطة: لا صفقة واضحة على هذا الفريم — السوق عرضي والانتظار أفضل."]
    market_session = facts.get("session") or {}
    if market_session.get("phase_ar"):
        extra = ""
        if market_session.get("minutes_to_close"):
            extra = f" (يتبقى {market_session['minutes_to_close']} دقيقة للإغلاق)"
        lines.append(f"🕒 حالة السوق: {market_session['phase_ar']}{extra}")
    if market_session.get("stale_note"):
        lines.append("⚠️ " + market_session["stale_note"])
    lines.append(f"⚠️ ما يلغي السيناريو: {verdict['invalidation']}")
    for conflict in verdict.get("conflicts") or []:
        lines.append("⚠️ " + conflict)
    if note:
        lines.append("ℹ️ " + note)

    items = (news or {}).get("headlines") or []
    if items:
        lines.append("📰 آخر العناوين:")
        for item in items[:3]:
            lines.append(f"• {item['title']}" + (f" — {item['publisher']}" if item.get("publisher") else ""))
    tilt = ((news or {}).get("social") or {}).get("tilt")
    if tilt:
        lines.append(f"🗣️ مزاج المتداولين على StockTwits: {tilt}")
    events = (news or {}).get("events") or {}
    if events.get("warning"):
        lines.append("⚠️ " + events["warning"])
    lines.append("")
    lines.append("هذا تحليل فني آلي وليس توصية استثمارية.")
    return "\n".join(lines)


def alert_text(*, symbol: str, name: str | None, frame_label: str, facts: dict,
               verdict: dict, events: dict | None = None) -> str:
    """The recommendation card posted automatically to the alerts topic.

    Template-only on purpose: a scan looks at dozens of symbols, and a model
    call per symbol would be slow, rate-limited and — worse — able to reword
    the numbers. The interactive answer is where the model earns its place.
    """
    trend = facts["trend"]
    mom = facts["momentum"]
    vol = facts["volume"]
    lv = facts["levels"]
    side_ar = "شراء" if verdict["side"] == "long" else "بيع"
    arrow = "🟢" if verdict["side"] == "long" else "🔴"
    title = f"{arrow} توصية {side_ar} — {symbol}"
    if name:
        title += f" ({name})"

    reasons = [f"{trend['structure_ar']} وترتيب المتوسطات {trend['ema_stack']}"]
    if trend.get("adx"):
        reasons.append(f"ADX {trend['adx']:.0f} يدل على اتجاه فعّال")
    reasons.append(f"RSI {mom['rsi']} و MACD {'إيجابي' if (mom.get('macd_hist') or 0) > 0 else 'سلبي'}")
    if vol.get("relative"):
        reasons.append(f"فوليوم {vol['relative']}× المعدل")
    if mom.get("rsi_divergence"):
        reasons.append(f"دايفرجنس {mom['rsi_divergence']}")
    if facts.get("patterns_ar"):
        reasons.append(facts["patterns_ar"][0])

    lines = [
        title,
        f"الفريم: {frame_label} | السعر الحالي: {facts['price']}",
        f"الثقة: {verdict['conviction']}% ({verdict['conviction_ar']})",
        "",
        "الأسباب:",
        *[f"• {r}" for r in reasons[:4]],
        "",
        f"الدخول: {verdict['entry']}",
        f"الستوب: {verdict['stop']} (مخاطرة {verdict['risk_pct']}%)",
        f"الأهداف: {'، '.join(str(t) for t in verdict['targets'][:3])}",
        f"العائد/المخاطرة: {verdict['rr']} | نسبة التعادل المطلوبة: {verdict['breakeven_rate']}%",
        f"⚠️ {verdict['invalidation']}",
    ]
    if lv.get("nearest_resistance") and verdict["side"] == "long":
        lines.append(f"أقرب مقاومة: {lv['nearest_resistance']}")
    if lv.get("nearest_support") and verdict["side"] == "short":
        lines.append(f"أقرب دعم: {lv['nearest_support']}")
    for conflict in (verdict.get("conflicts") or [])[:2]:
        lines.append("⚠️ " + conflict)
    if (events or {}).get("warning"):
        lines.append("⚠️ " + events["warning"])
    session_state = facts.get("session") or {}
    if session_state.get("phase_ar"):
        lines.append(f"🕒 {session_state['phase_ar']}")
    lines.append("")
    lines.append("تحليل فني آلي — ليس توصية استثمارية. لا تكبّر المخاطرة.")
    return "\n".join(lines)
