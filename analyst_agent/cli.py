"""Command-line runner — test the whole pipeline without Telegram.

    python -m analyst_agent.cli TSLA 15m
    python -m analyst_agent.cli --image shot.png "حلل 4 ساعات"
    python -m analyst_agent.cli 2222.SR 1d --out /tmp/aramco.png --json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from . import analyst, frames


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chart analyst agent (CLI)")
    parser.add_argument("request", nargs="*", help="مثال: TSLA 15m  أو  \"حلل أرامكو يومي\"")
    parser.add_argument("--image", help="صورة تشارت لتحليلها (تُقرأ عبر Groq vision)")
    parser.add_argument("--out", default="chart.png", help="مسار حفظ التشارت الناتج")
    parser.add_argument("--frame", help="فريم افتراضي إن لم يُذكر في الطلب")
    parser.add_argument("--no-news", action="store_true", help="تجاهل الأخبار والمزاج")
    parser.add_argument("--json", action="store_true", help="اطبع تفاصيل التشخيص")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    caption = " ".join(args.request).strip() or None
    image = pathlib.Path(args.image).read_bytes() if args.image else None
    if not caption and not image:
        parser.print_help()
        return 2

    answer = analyst.analyze(caption, image, default_frame=args.frame,
                             with_news=not args.no_news)
    print(answer.text)
    if answer.chart_png:
        out = pathlib.Path(args.out)
        out.write_bytes(answer.chart_png)
        print(f"\n[chart saved: {out}  ({len(answer.chart_png) // 1024} KB)]")
    if args.json:
        print("\n[debug]")
        print(json.dumps(answer.debug, ensure_ascii=False, indent=2, default=str))
        print("model:", answer.used_model, "| frames:", " ".join(frames.all_keys()))
    return 0 if answer.ok else 1


if __name__ == "__main__":
    sys.exit(main())
