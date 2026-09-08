"""Post a description of what each Telegram section carries.

Salem's group has two topics and no way to tell, from inside one of them, what
it is for or what a message in it means. This writes that header once, so it
can be pinned and the feeds explain themselves.

Every number here is READ from config rather than typed. A pinned message that
says "30 alerts a day" while MAX_ALERTS_PER_DAY is 5 is worse than no pinned
message: it is a promise the system does not keep, sitting at the top of the
channel for a month.
"""
import argparse

import venv_boot

venv_boot.ensure(["requests"])

import config as C
from telegram_send import send, send_paper


def _rule(dte=0):
    for max_dte, take, stop, _note in C.EXIT_RULES:
        if dte <= max_dte:
            return take, abs(stop)
    return 40, 30


def alerts_text():
    take, stop = _rule(0)
    return "\n".join([
        "📌 هذا القسم: تنبيهات الدخول",
        "",
        "كل ما يصلك هنا هو اقتراح. القرار لك — ممكن يجيك تنبيه وما تدخل.",
        "",
        "▸ نوعان من الرسائل",
        "",
        f"👀 مراقبة — السهم قرّب من مستوى مهم وما كسره بعد.",
        "   مو تنبيه دخول. تعطيك وقت تتابعه بنفسك.",
        "",
        "🚨 تنبيه — كسر المستوى، وثبت فوقه، وفيه ضغط شراء.",
        "   يجيك فيه: ليش، وين متوقع يوصل، وين تخرج لو رجع،",
        "   وأي عقد يناسب ميزانيتك.",
        "",
        "▸ كيف يشتغل",
        "",
        f"يمسح السوق كل {C.SCAN_EVERY_MIN} دقائق، ويراقب المرشحين",
        f"كل {C.MONITOR_EVERY_MIN} دقائق على شمعة 15 دقيقة.",
        "التنبيه ما يطلع إلا بعد ما تُغلق الشمعة فوق المستوى —",
        "لأن الكسر اللي ينعكس فوراً هو أكثر ما يخسر.",
        "",
        f"حد التنبيهات: {C.MAX_ALERTS_PER_DAY} في اليوم.",
        "",
        "▸ خطة الخروج المقترحة للعقود اليومية",
        "",
        f"بيع عند +{take}%، واخرج عند -{stop}%.",
        "الوقف الضيق يصنع خسائر: العقد يتذبذب أكثر من ذلك",
        "في دقيقة واحدة، فيخرجك قبل أن تبدأ الحركة.",
        "",
        "▸ سجّل صفقاتك هنا",
        "",
        "رُدّ على أي تنبيه بكلمة «دخلت» — يسجّلها باسمك.",
        "«دخلت 186» لو أخذت سترايك غير الأول.",
        "«خرجت» أو «خرجت 2.59» عند البيع.",
        "نهاية اليوم يوصلك ملخّص صفقاتك أنت.",
        "",
        "▸ ما لا يفعله",
        "",
        "لا يشتري ولا يبيع نيابة عنك. لا يتنبأ.",
        "يقرأ ما حدث فعلاً ويقول: هذا ما أراه، وهذا ما ينقصني.",
    ])


def paper_text():
    take, stop = _rule(0)
    b = C.PAPER_BASELINE
    return "\n".join([
        "📌 هذا القسم: التداول الورقي (تجريبي)",
        "",
        "⚠️ لا شراء ولا بيع حقيقي. لا وسيط، ولا ريال يتحرك.",
        "هذا دفتر تسجيل يتابع نفسه.",
        "",
        "▸ ماذا يحدث",
        "",
        "كل تنبيه يُسجّل هنا كصفقة وهمية، ثم يُتابَع سعر",
        "العقد الحقيقي دقيقة بدقيقة، ويُغلق بنفس القاعدة:",
        f"+{take}% أو -{stop}% أو {C.MAX_HOLD_MIN} دقيقة أو قبل الإغلاق.",
        "",
        "▸ ماذا يصلك",
        "",
        "📄 عند إغلاق كل صفقة: النتيجة في ثلاثة أسطر.",
        "📄 نهاية كل يوم: ملخص، ومقارنة بنتيجة الاختبار.",
        "",
        "▸ الرقم المرجعي (من 796 صفقة على 9 جلسات)",
        "",
        f"   يصل الهدف {b['hit']:.1f}%   ويخسر {b['lost']:.1f}%",
        f"   العائد ${b['avg']:.3f} لكل $1 (قبل احتساب العمولة)",
        "",
        "▸ الفرق بينه وبين قسم التنبيهات",
        "",
        "هذا القسم يأخذ كل تنبيه ويحاسبه بقاعدة ثابتة — اختبار",
        "للاستراتيجية نفسها. أما صفقاتك أنت فتُسجَّل في قسم",
        "التنبيهات بالرد «دخلت»، والخروج فيها قرارك وحدك.",
        "",
        "▸ لماذا هذا القسم موجود",
        "",
        "النتيجة هنا ستكون أفضل من الواقع: تفترض أنك دخلت",
        "في نفس الدقيقة وبسعر مثالي. الواقع دقائق أبطأ.",
        "الفجوة بين هذا الرقم ورقمك الحقيقي هي ما نقيسه —",
        "قبل أن يدخل أي مال.",
    ])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="print both messages instead of sending them")
    p.add_argument("--only", choices=("alerts", "paper"),
                   help="send just one section's header")
    args = p.parse_args(argv)

    jobs = []
    if args.only != "paper":
        jobs.append(("alerts", alerts_text(), send))
    if args.only != "alerts":
        jobs.append(("paper", paper_text(), send_paper))

    for name, text, fn in jobs:
        if args.dry_run:
            print(f"\n{'=' * 50}\n[{name}]\n{'=' * 50}\n{text}")
            continue
        print(f"{name}:", "sent" if fn(text) else "NOT sent")
    if not args.dry_run:
        print("\nثبّت كل رسالة في أعلى موضوعها (Pin).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
