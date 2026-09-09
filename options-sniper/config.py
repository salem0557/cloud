"""Central configuration — edit values here, not inside the scripts."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Writable location for state and the journal. On Railway this points at the
# mounted volume (set SNIPER_DATA_DIR=/data); everywhere else it is the project
# folder. Railway containers have an ephemeral filesystem, so without the volume
# every redeploy would wipe the daily counter and the paper-trading history.
DATA_DIR = Path(os.environ.get("SNIPER_DATA_DIR") or Path(__file__).parent)


def _load_env():
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# ── API keys (loaded from .env) ─────────────────────────────────
# The .env.example placeholders are shipped in the repo, and Railway's
# "Suggested Variables" panel offers to import them verbatim. A placeholder is
# a non-empty string, so an emptiness check would pass and the first UW call
# would fail with a bare 401 every 30 minutes instead of saying why.
_PLACEHOLDER_MARKERS = ("ضع_", "your-token-here", "your_username", "_هنا")


def _clean(name):
    v = os.environ.get(name, "").strip()
    if v and any(m in v for m in _PLACEHOLDER_MARKERS):
        return ""
    return v


UW_API_KEY       = _clean("UW_API_KEY")
UW_BASE          = "https://api.unusualwhales.com"
FINVIZ_AUTH      = _clean("FINVIZ_AUTH")
TELEGRAM_TOKEN   = _clean("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _clean("TELEGRAM_CHAT_ID")
# Optional second destination for the paper record. A month of "closed +40% in
# 4 minutes" would otherwise bury the handful of alerts Salem acts on. Unset,
# both go to the same chat.
TELEGRAM_PAPER_CHAT_ID = _clean("TELEGRAM_PAPER_CHAT_ID")
# Salem's two destinations turned out to be two TOPICS in one forum group, not
# two chats: t.me/c/<group>/943 for alerts and .../944 for the paper record
# share the group id and differ only in the topic. Telegram routes that with
# message_thread_id, so without these both topics get the group's General.
TELEGRAM_TOPIC_ID       = _clean("TELEGRAM_TOPIC_ID")
TELEGRAM_PAPER_TOPIC_ID = _clean("TELEGRAM_PAPER_TOPIC_ID")

# ── Scoring (agreed design: 30/30/20/20) ────────────────────────
WEIGHTS = {"flow": 30, "technical": 30, "catalyst": 20, "liquidity": 20}

# THRESHOLD was 85 out of 100, and the first live session produced no alert at
# all — and therefore no paper trade either, because the paper book only opens
# on an alert. That was not bad luck. Two measurements, not opinions:
#
#   1. Without an aligned news catalyst the maximum reachable score is 80.
#      flow 30 + technical 30 + catalyst 0 + liquidity 20 = 80 < 85. So 85
#      made a news catalyst mandatory on every single alert — a rule nobody
#      chose, sitting in the arithmetic.
#   2. On 50 live UW flow alerts (2026-09-08, 10:17 ET) exactly ONE ticker of
#      34 could reach 85 even in theory, and only with technical, catalyst and
#      liquidity all perfect at the same moment. Measured flow scores: BE 21.2,
#      DRAM 11.5, SMCI 11.2, AMD 10.2, MU 9.5, SPX 8.8 — out of 30.
#
# And the setup Salem describes — good news, a buy wave, a clean break of
# resistance — scores 64.5 on realistic inputs. Twenty points short.
#
# The old comment said "calibrate from journal.csv after 2-4 weeks paper".
# That could never happen: the journal only fills from alerts, and there were
# none. 70 is the number that starts the loop. It is a starting point to be
# re-derived from real results, not a settled figure.
# 65 was still above what a real setup produces. Measured on BE at 09:45 ET
# on 2026-09-08 — the strongest tape of the day, a textbook break on 4.86x
# volume — with every number read from UW: flow 21.2 + technical 29.5 +
# catalyst 0 + liquidity 8.0 = 58.7. It cleared 65 only if a news catalyst
# happened to land too. Salem asked for it loosened until an alert arrives.

# ══ HALVED FROM THE ORIGINAL DESIGN, ON SALEM'S INSTRUCTION ══════
# "خفضها الى نص ماكانت عليه قبل اي تعديل مثلا العتبة كانت 85 اجعلها 45".
# Every MINIMUM gate below is half of the value it shipped with, before any
# tuning in this session. Maximum limits are untouched: halving a ceiling
# tightens it, which is the opposite of what was asked.
#
#   gate                     shipped    now
#   THRESHOLD                     85     45
#   WATCHLIST_FLOOR               65     30
#   MIN_REMAINING_ATR           0.75   0.38
#   VOLUME_SPIKE_RATIO           1.5   0.75
#   MIN_OPEN_INTEREST            300    150
#   MIN_ASK_SIDE_RATIO          0.55   0.28
#   MIN_DIRECTIONAL_SHARE       0.60   0.30
#   MIN_MINUTES_TO_CLOSE          45     23
#   MIN_TICKER_PREMIUM       250,000  100,000  (already below half)
#
# These are volume settings, not measurements. Nothing here was derived from
# a result — the paper book in 944 is what will say where they belong.
# Salem, after the candle fix let the scanner reach these gates for the first
# time: keep the loose gate for the paper book, raise the one he acts on.
# 943 is money he decides on; 944 is data collection, and the wider it is the
# faster the two populations can say where 943 belongs.
# TWO GATES, BY KIND OF SETUP — not one number for both.
#
# THRESHOLD is for a setup with NO confirmed break: flow, a headline and a
# liquid contract, and nothing in the price agreeing yet. That is a claim about
# what might happen, so it has to be exceptional.
#
# BREAK_THRESHOLD is for a setup where the stock HAS broken its level on volume
# and held it. Price is the one thing that cannot be talked into agreeing, and
# it is the whole reason Salem wants alerts: "أي تحرك بالسهم يكسبني مال".
# Making a confirmed break clear the same bar as a rumour was backwards.
#
# Measured on the live 16:56Z scan (41 names, real scores). With a break adding
# a realistic 25 technical points:
#
#     gate 70   10 of 41 could alert
#     gate 60   20 of 41
#     gate 55   27 of 41
#     gate 50   34 of 41
#
# In WATCHLIST_ONLY mode the gate is not a score at all. Salem named the signal
# himself: a 15m break of resistance or support, with volume in the stock AND
# money on that side in the options, and then the three contracts. So that is
# the rule — technical.confirms() plus option flow agreeing — and the score is
# still computed and printed, because it is what the paper book compares, but
# it does not decide whether the alert is sent.
#
# The reason to drop the score as a gate: it mixed a headline, a spread and a
# premium total into one number, and on 2026-09-08 it let ten alerts through
# with technical 0.0 — no break at all — while silencing TSLA and NVDA while
# they ran. A break IS the signal; the rest is context.
THRESHOLD          = 70     # no break: has to be exceptional (discovery mode)
BREAK_THRESHOLD    = 50     # price already confirmed it
# The paper book's own gate, deliberately looser than the alert gate. Salem
# asked for both to be loosened and the paper one loosened further: "سهل
# الشروط على كل الاثنين لكن الورقي سهلها اكثر". Everything scoring between
# PAPER_THRESHOLD and THRESHOLD is opened in 944 and never sent to 943, so a
# month of results says what the alert gate would have earned at 55, at 60,
# at 65 — measured, instead of argued.
PAPER_THRESHOLD    = 35     # below BOTH gates, so 944 always has a
                            # population to compare 943 against
# Kept 20 below the threshold, as it was at 85/65. The gap is what lets a
# ticker with strong flow but no break yet sit on the watchlist until the
# break arrives and monitor.py adds the technical points.
# A watched ticker's score when it finally breaks is base + technical, and
# technical is worth at most WEIGHTS["technical"]. So a name whose base sits
# below THRESHOLD - 30 can never reach the gate however cleanly it breaks —
# it is watched all day, it sends its heads-up, and it is incapable of
# becoming an alert. At 30 that was everything scoring 30-39: messages Salem
# could do nothing with, which is all he received on 2026-09-08.
#
# The floor is now derived, not chosen. Raise THRESHOLD and it follows.
# Derived from the LOWER gate: a watched name is watched precisely because it
# has not broken yet, so the gate it will be judged by when it does is
# BREAK_THRESHOLD. Deriving from THRESHOLD would have kept out every name that
# a break could promote.
WATCHLIST_FLOOR    = min(THRESHOLD, BREAK_THRESHOLD) - WEIGHTS["technical"]  # 20
# The THRESHOLD is the quality gate; this is only a volume limit. The scanner
# sorts candidates by score and stops below the threshold, so raising this does
# not lower the quality of any single alert — it stops discarding setups that
# qualified but arrived late in the day. It does mean a losing strategy loses
# six times faster at 30 than at 5, which is the reason it exists.
MAX_ALERTS_PER_DAY = int(os.environ.get("MAX_ALERTS_PER_DAY") or 5)

# ── Alerting the same name twice ────────────────────────────────
# A ticker used to be locked out for the rest of the day the moment it alerted
# once. Salem: "كيف افك هذا القيد ليعطيني كسور متكررة اقوى". On 2026-09-08 TSLA
# broke at 09:45 and again, harder, hours later — only the first would have
# reached him, and the second was the better trade.
#
# The lock is not removed, it is made conditional. Simply removing it re-sends
# the SAME break every scan: the level barely moves, so the same setup would
# clear the gate again ten minutes later.
#
# A second alert on a name needs BOTH:
#   - REALERT_COOLDOWN_MIN since the last one, so one move is one alert, and
#   - a level at least REALERT_LEVEL_ATR beyond the level already alerted,
#     in the same direction — a genuinely higher high, not the same one again.
# A direction FLIP always qualifies: a name that broke up in the morning and
# breaks down in the afternoon is a different trade, not a repeat.
REALERT                = os.environ.get("REALERT", "1").lower() in ("1", "true", "yes")
REALERT_COOLDOWN_MIN   = 45
REALERT_LEVEL_ATR      = 0.5
MAX_ALERTS_PER_TICKER  = 3    # one runaway name must not eat the day's quota

# ── Budget bands (contract cost = ask x 100) ────────────────────
# Each band is a RANGE with a floor and a ceiling, not a target price. The old
# tiers were nested caps — every contract under $50 also qualified for the $100
# and $200 tiers — so which tier a contract landed in came down to the order
# they were checked, not to what it cost. And each tier picked by character
# (deepest ITM, nearest the money, cheapest OTM) rather than by what the
# contract was actually worth buying.
#
# Bands are now disjoint, and the pick inside each is the best contract by
# quality: expected profit, liquidity, and whether the stock can reach the
# strike at all.
BUDGET_TIERS = [
    ("🟢 <200$", 100, 200),   # (label, floor cost, ceiling cost)
    ("🟡 <100$", 50, 100),
    ("🔴 <50$", 0, 50),
]

# Weights for choosing inside a band. Expected profit is the point, but a
# contract nobody will trade back to you, or one whose strike the stock cannot
# reach, is not worth its headline number.
QUALITY_WEIGHTS = {"profit": 0.5, "liquidity": 0.3, "reach": 0.2}
REACHABLE_ATR = 2.0          # ATRs to the strike that still counts as reachable
MAX_PROFIT_CREDIT = 300.0    # cap on the profit term: a 900% estimate on a
                             # far-OTM contract is delta arithmetic, not an edge

# ── 15m technical frame ─────────────────────────────────────────
CANDLE_SIZE        = "15m"
CANDLES_LOOKBACK   = 40     # bars used for ATR and the volume average
# The breakout LEVEL is read from a shorter window, cut at this session's open.
# 12 bars of 15m is three hours of intraday structure. At 40 the level reached
# back into the previous session: on 2026-09-08 TSLA gapped down, rallied 355.80
# -> 370.00 (+3.3%, its 370 call 1.09 -> 3.75) and the scanner called it "no
# break" the whole way, because Sep 3's high of 384.04 was still the level.
LEVEL_LOOKBACK     = 12     # bars the breakout level may be read from
LEVEL_MIN_BARS     = 3      # below this the session has no structure yet, so
                            # the opening-range rule below takes over

# ── Opening range (09:30-10:00 ET) ──────────────────────────────
# The session's first bars are where the day's biggest moves start, and the
# ordinary rule cannot see them: it needs LEVEL_MIN_BARS closed before there is
# any intraday structure to break, which is 10:15 at the earliest.
#
# 2026-09-08 measured the cost. NVDA opened 233.24 and fell to 225.81; its 225
# put went 0.25 -> 1.60. TSLA opened 357.25 and ran to 370.00; its 370 call went
# 1.09 -> 3.75. Both moves were most of the way done before 10:15.
#
# So for that window only, the level is the first OPENING_RANGE_BARS bars —
# the opening range every intraday desk watches — and a break of it counts.
# The open is noisier than the rest of the session, so it is NOT the same trade:
# the volume requirement is higher, and the alert is tagged so Salem can see at
# a glance that it is the faster and riskier kind.
USE_OPENING_RANGE     = os.environ.get("USE_OPENING_RANGE", "1").lower() in ("1", "true", "yes")
OPENING_RANGE_BARS    = 2     # 09:30-10:00 on the 15m frame
OPENING_VOLUME_RATIO  = 1.5   # vs VOLUME_SPIKE_RATIO for the rest of the day

# ── The failed break (reversal) ─────────────────────────────────
# Salem, 2026-09-09, on the MSFT 495 call: it fell 4.23 -> 1.19 as the stock
# sold off, then ran 1.19 -> 2.30 (+93%) on the bounce. "كيف تجعل استراتيجيتنا
# تعمل لاخذ العقد بالاسفل وبيعه بالاعلى".
#
# The breakout rule cannot see that trade, and not by accident: while the stock
# was making its low it was producing a PUT signal, and the moment he wants —
# the bottom — is that put signal FAILING.
#
# So the failed break is its own signal. A bar that pierces support and CLOSES
# BACK ABOVE it, in the top third of its own range, on volume: sellers pushed
# through the level and could not hold it, and the ones who sold the break are
# now trapped. Mirrored at the top for puts.
#
# Measured on NVDA's 15m tape, 5 sessions: TWO signals. That is not evidence of
# anything and it is not treated as any — it is tagged separately so 944
# measures it apart from the breakout, and after a month the book says which of
# the two earns its place.
USE_REVERSAL          = os.environ.get("USE_REVERSAL", "1").lower() in ("1", "true", "yes")
REVERSAL_VOLUME_RATIO = 0.75  # the reclaim needs buyers behind it
REVERSAL_CLOSE_THIRD  = 1 / 3 # close in the third of the bar that agrees
# How many OHLC pages uw.candles() may walk back to reach that many REGULAR
# bars. One page is not enough: UW answers timeframe=5D with 100 rows and no
# more, ~60 of them pre/post-market, which left 39 usable against the 40
# needed — every liquid ticker, every scan. Two pages give roughly 80.
CANDLE_PAGES       = 3
ATR_PERIOD         = 14
# Half of 1.5. Below 1.0 this stops being a volume filter at all: a bar with
# LESS volume than the prior average now confirms a break. That is what halving
# it means, and it is the first thing to put back if the alerts read thin.
VOLUME_SPIKE_RATIO = 0.75   # candle volume vs prior-bar average
TARGET_ATR_MULT    = 1.5    # target = broken level +/- 1.5 x ATR
STOP_ATR_MULT      = 1.0    # stop   = broken level -/+ 1.0 x ATR
# How much of the measured move must still be ahead of price to alert. At
# 0.75 this rejected the two strongest names in the market on 2026-09-08 —
# BE at +0.56 ATR and AMD at +0.71 — and the day produced no alert at all.
MIN_REMAINING_ATR  = 0.38   # half of 0.75
# The paper book takes anything with room still ahead of it. Below this it is
# not a setup, it is the top: price has effectively reached its target and an
# entry has nothing left to collect.
PAPER_MIN_REMAINING_ATR = 0.05
# A break that still has room toward its target, but less than the line above
# demands, is NOT alerted — Salem never sees it in 943. It is opened in the
# paper book only, so a month of results answers the question the rule cannot
# answer about itself: does 0.75 protect him, or does it cost him?
#
# Measured 2026-09-08, the two strongest names in the market, both rejected by
# a hair and both of which would have lost money:
#   BE  09:45  +0.56 ATR left  (close 278.44 -> 276 within the hour)
#   AMD 10:15  +0.71 ATR left  (close 500.00 -> 499)
# One session is not evidence. The paper book is how it becomes evidence.
#
# A break that has passed its target (remaining < 0) is never taken, on paper
# or otherwise. That is not a near miss, it is buying the top.
PAPER_NEAR_MISS = os.environ.get("PAPER_NEAR_MISS", "1").lower() in ("1", "true", "yes")

# ── The early notice: "this one is coiling" ─────────────────────
# Salem's actual goal, stated from the first message: ride the contract's rise
# from the start, not after the break has been confirmed and 0.3 ATR is
# already gone. A confirmed break is the measured signal and stays the only
# thing called an alert; this is a heads-up on a name approaching its level,
# so he can watch it himself and decide to be early. Set WATCH_NOTICE = 0 to
# turn it off.
WATCH_NOTICE       = int(os.environ.get("WATCH_NOTICE") or 1)
APPROACH_ATR       = 0.60    # within this much of the level -> worth watching
MIN_MAGNET_SHARE   = 0.15    # a strike taking under 15% of net flow is noise
# Share alone is not enough: 100% of nothing is still nothing. Three of the
# five watch notices sent on 2026-09-08 read "صافي 0.0M$ شراء، 100% من تدفق
# اليوم" — a strike with no money behind it, presented as where the money is.
MIN_MAGNET_PREMIUM = 250_000
# And a strike 35% away from price is not a magnet, it is a lottery ticket.
# LYTE's notice pointed at one 35.1% away, DYN's at 20.8%, GH's at 10.7%.
# Nothing that far can be reached inside the hold this system trades.
MAX_MAGNET_DISTANCE_PCT = 8.0
# The good notice has to be findable. Five at once on obscure names buries it.
MAX_WATCH_PER_DAY  = 5
                            # Without it the scanner alerts on breakouts that have
                            # ALREADY run to target: price $102.40, target $102.58,
                            # 18c of room left and an "expected profit" of 6%.
                            # A late entry costs more than a false one.
REGULAR_HOURS_ONLY = True   # ignore pre/post-market candles (market_time == "r")

# ── Exit rules for open positions ───────────────────────────────
# One set of numbers cannot serve both a same-day contract and a 6-week one.
# A 0DTE position has no tomorrow to recover in, so it takes profit earlier,
# cuts earlier, and is closed on the clock whatever it is doing. A longer-dated
# contract can be given room, because theta is not taking it apart today.
#
# STARTING POINTS, not conclusions. After ~20 logged alerts, fill `outcome`
# and `result_pct` in journal.csv and set these from your own results:
# if most winners ran well past the take level you cut too early; if most
# losers passed the stop before reversing you cut too late.
# The 0DTE row is measured, not guessed. Salem accepted a wider pair on one
# condition: that more trades win. Across 9 usable sessions and 796 gated
# trades, +40/-30 is the pair that best answers it —
#
#   pair        reaches target   does not lose   per $1 (one vote per session)
#   +50/-35          32.8%           59.7%             $1.035
#   +40/-30          43.2%           58.8%             $1.033
#
# ten points more of the trades actually reach the target, for two cents per
# thousand dollars. Both hold their value through the 25% slippage column and
# both won 5 sessions of 9.
#
# Neither meets his 35% loss-rate target: the measured rate is 41.2%, and
# MAX_STOP_PCT caps the stop at 35 because he ruled out anything wider as
# gambling. And $1.033 with every session weighted equally is a 3.3% edge on
# 41 distinct contracts — enough to paper trade, not enough to trust.
EXIT_RULES = [
    # (max_dte, take_profit_pct, stop_loss_pct, note)
    (0,   40, -30, "0DTE: اخرج بالكامل عند +40% — لا يوجد غد"),
    (7,   60, -40, "بيع نصف الكمية عند الهدف وارفع الوقف إلى سعر الدخول"),
    (999, 80, -40, "بيع ثلث الكمية عند الهدف واترك الباقي بوقف متحرك"),
]

# Same-day contracts are closed on the clock regardless of P&L: whatever is
# left of a 0DTE contract at 16:00 ET is worth its intrinsic value and nothing
# more, and the last half hour is where that collapse happens fastest.
ZERO_DTE_HARD_EXIT_ET = "15:30"

# Kept for anything that does not resolve to a rule above.
PROFIT_TAKE_PCT = 60
STOP_LOSS_PCT   = -40

# ── Liquidity minimums (contracts failing these are dropped) ────
MAX_SPREAD_PCT    = 8       # (ask-bid)/mid * 100
MAX_SPREAD_ABS    = 0.06    # ...OR this many dollars wide, whichever is kinder.
                            # A $0.45 contract with a normal 4c spread is 9% and
                            # would fail a pure percentage cap — which emptied the
                            # 🔴 OTM tier on almost every scan. Cheap contracts are
                            # judged in cents, expensive ones in percent.
# 300 dropped 5 of 19 live BE strikes on 2026-09-08, among them the cheap far
# strikes Salem trades. A same-day contract's open interest is thin by nature.
MIN_OPEN_INTEREST = 150

# ── Contract selection window ───────────────────────────────────
MIN_DTE = 0                 # 0 = same-day expiry (0DTE) allowed — Salem's call
MAX_DTE = 45

# 0DTE-specific. A same-day contract loses its remaining value into the close,
# so an entry taken late in the session needs the move to happen almost at once.
# Set to 0 to disable the cutoff entirely.
MIN_MINUTES_TO_CLOSE = 23   # half of 45 — no new 0DTE alert inside this window

# Assumed holding time, in trading hours, used only to price theta into the
# profit estimate. The 15m breakout is expected to resolve within a couple of
# bars; raise it if you hold longer.
HOLD_HOURS = 2.0
TRADING_HOURS_PER_DAY = 6.5
# 15m bars in one regular session — the unit a same-day move is measured in
BARS_PER_SESSION = int(TRADING_HOURS_PER_DAY * 60 / 15)      # 26

# ── Paper trading (paper.py) ────────────────────────────────────
# Scored by the same entry_exit() the backtest used, so live and measured are
# comparable. The baseline is what 796 gated trades over 9 sessions said at
# +40/-30; if the paper month lands far below it, the backtest was measuring
# its own assumptions and that is the thing worth knowing.
# How long a same-day trade is given before it is abandoned. ONE constant, so
# the paper book, the backtest default and the alert cannot drift apart.
#
# Salem raised it from 15 to 30 after the timeout finding: a third to a half
# of gated trades were timing out, and the median winner took 8-10 minutes
# against a 15-minute limit, so a trade needing 16 was being recorded as a
# failed setup when it was a failed deadline.
#
# This is his call and it is made BEFORE the measurement confirms it. Holding
# a same-day contract longer is not free -- theta is the entire reason a
# deadline exists -- so the hold table in zero_dte.py is what says whether it
# was right. If 30 returns less than 15 there, this comes back down.
MAX_HOLD_MIN = int(os.environ.get("MAX_HOLD_MIN") or 30)
PAPER_MAX_HOLD = MAX_HOLD_MIN
PAPER_HARD_EXIT = "15:30"
PAPER_MIN_TRADES = 30        # below this the record says nothing either way
# Measured at +40/-30 -- the pair EXIT_RULES[0] actually uses -- over 1,612
# gated trades across 15 sessions, commission charged. It REPLACES a baseline
# of 43.2/41.2/$1.033 that was taken before the clock was fixed: that run read
# UW's UTC timestamps as New York, so it stopped entering at 11:30 in the
# morning and measured two hours of the session while claiming to measure the
# day. Comparing a live paper month against it would have been comparing
# against a different experiment.
PAPER_BASELINE = {"hit": 31.3, "lost": 57.1, "avg": 0.994}
# What the same run said about the pair the walk-forward settled on, +60/-35:
# $1.010 out of sample across 8 sessions, 5 of them profitable -- the only
# figure here not helped by knowing the answer first. EXIT_RULES is Salem's
# call, so nothing is changed on his behalf; this is recorded so the choice is
# made against a number rather than a memory.
WALK_FORWARD = {"pair": "+60/-35", "avg": 1.010, "sessions": 8, "won": 5}

# ── Settled questions, so they are not re-litigated ─────────────
# SKIPPING THE MIDDAY WINDOW: tested and REJECTED.
#
# The midday returned $0.79-$0.90 in five separate sessions while momentum
# returned $1.14-$1.38, so --skip-windows midday was built to test it. Run at
# +60/-35 across the same 20 sessions:
#
#                          pooled      walk-forward
#     everything          $1.021        $1.010   5/8 sessions
#     midday skipped      $1.087        $0.979   4/7 sessions
#
# The pooled figure went UP and the honest one went DOWN. Skipping removed 486
# trades; the two busiest sessions improved and lifted the pooled average,
# while 2026-08-27 fell $0.906 -> $0.750, 08-17 $0.773 -> $0.696 and 08-20
# $0.821 -> $0.787 -- on those days the midday trades were the best on offer,
# and removing them left the worst. The chosen pair also became less stable
# (3 changes over 9 decisions, against 2 over 10).
#
# The pattern was real in those sessions and did not repeat. Acting on it
# would have cost about three cents per dollar while showing a nicer table.
# The flag stays, because the next hypothesis deserves the same test.
# RAISING VOLUME_SPIKE_RATIO ON THE WATCHLIST STRATEGY: not supported.
#
# Measured 2026-09-09 on NVDA's real 15m tape, 130 bars over 5 sessions, by
# walking every bar, taking is_signal() at face value, and reading the stock's
# MFE/MAE over the 30-minute hold:
#
#     vol ratio   signals   ran   ran%   med MFE   med MAE
#          0.75         6     3    50%     +0.59     +0.82
#          1.00         4     1    25%     +0.36     +1.32
#          1.30         4     1    25%     +0.36     +1.32
#          1.50         3     1    33%     +0.36     +0.82
#
# Asking for more volume did not separate the runners from the fades — it
# removed a winner. I had recommended putting this back to 1.3, and this does
# not support it. That was reasoning, not measurement, and it is recorded as
# such rather than quietly dropped.
#
# SIX SIGNALS IS NOT A RESULT, and nothing is changed on the strength of it.
# What the sample does say is worth writing down: all three that ran were on
# 2026-09-08, a trending day; all three that faded were on 09-03 and 09-04,
# choppy ones. And the MEDIAN DRAWDOWN (0.82 ATR) EXCEEDED THE MEDIAN GAIN
# (0.59). On this evidence the exit rule decides the outcome, not the entry.
SETTLED = {"raise volume filter": "not supported on 6 signals: 0.75 ran 50%, "
                                  "1.30 ran 25%; sample far too small to act on",
           "skip midday": "rejected: pooled $1.021->$1.087 but "
                          "walk-forward $1.010->$0.979"}

# ── What no desk would go live without ──────────────────────────
# Per contract, per side. $0.65 is the common retail rate; some brokers charge
# $0.50, a few $0. On a $0.95 contract that is 0.7% each way, 1.4% round trip
# — a third of the whole measured edge, and nothing had charged it. The
# backtest and the paper book both charge it now.
COMMISSION_PER_CONTRACT = float(os.environ.get("COMMISSION_PER_CONTRACT") or 0.65)

# These two do NOT block an alert. Salem wants all 30 of the best setups and
# picks his own entries — an alert is information, and withholding information
# because a PAPER position lost is the wrong trade-off entirely. They add a
# warning line to the message, and they gate the paper book, which has to stay
# a record of a disciplined trader rather than of thirty correlated bets.
#
# The paper book stops taking new positions once it is down this much on the
# day. At 30 alerts and a 41% loss rate a bad session is a certainty, and a
# book that keeps opening into one stops measuring the rule. 0 disables.
MAX_DAILY_LOSS_USD = float(os.environ.get("MAX_DAILY_LOSS_USD") or 300)

# Thirty calls on a rally day are one bet placed thirty times, not thirty
# bets. The paper book holds at this many open on one side; the alert still
# goes out, carrying the count so Salem can see the crowding himself.
MAX_SAME_DIRECTION_OPEN = int(os.environ.get("MAX_SAME_DIRECTION_OPEN") or 4)

# ── The three gates Salem's previous system had and our test excluded ──
# Its 0DTE names quote 1-3% wide against the 10-25% of the population we
# measured, and the spread is what decides whether a +40% target is reachable:
# at 2% a contract must travel +43%, at 15% it must travel +63%.
LIQUID_0DTE = ["SPY", "QQQ", "IWM", "NVDA", "TSLA"]

# No chasing: an entry more than this far past the breakout level is a late
# entry into a move that already happened. The old system used 0.30.
MAX_CHASE_ATR = 0.30

# The same idea, but on the CONTRACT instead of the stock, and for the gap
# between the alert being sent and Salem reading it. In his words: "لي ان
# شاهدته ارتفع قبل دخولي اتجاهله". Every measured figure in this project
# assumes entry at the price shown in the alert; pay 10% more and the +40%
# target becomes about +27% off a base that is 10% higher, while the stop sits
# further away in dollars. So the message prints a ceiling.
#
# Be clear about what this number is: 10 is a POLICY, not a measurement. The
# backtest entered at the signal minute and never simulated a late fill, so
# nothing here measured what chasing the contract costs. It is set at the
# level where the arithmetic above stops resembling what was measured.
MAX_CHASE_PCT = float(os.environ.get("MAX_CHASE_PCT") or 10)

# Four independent reads - trend, momentum, VWAP side, structure - and at least
# this many must agree. Below it the old system stayed silent rather than send
# a weak signal, which is the right default for an alert Salem acts on.
MIN_AGREEMENT = 3

# (start hour, end hour, name) in Eastern decimal hours.
SESSION_WINDOWS = [
    (9.5,  10.0, "open"),          # opening auction chop
    (10.0, 11.5, "momentum"),      # the window the old system trusted most
    (11.5, 13.5, "midday"),        # chop
    (13.5, 15.0, "trend"),
    (15.0, 15.5, "gamma"),         # theta and gamma both bite here
]

# ── Scan limits (UW trial = 30,000 requests/day) ────────────────
# ── How wide the net is thrown ──────────────────────────────────
# Salem asked to widen this, and the reason is in the measurement, not in a
# preference: in the 20-session test 9 sessions produced ZERO qualified
# entries, and every session that lost had the rule firing on only 1-2
# contracts. Two contracts in a day is luck, not a result. The sessions with
# five or more contracts all made money.
#
# Scanning "the whole market" literally is not on the table: 6,000 tickers x 3
# requests x 39 scans a day is ~700,000 requests against a 30,000 allowance.
# But the market-wide part is already nearly free — ONE request to the flow
# alerts feed sees every ticker UW flags. The cost is entirely in the
# per-candidate work afterwards (candles + chain + news = 3 requests each).
#
# So the widening is split: raise the free part a lot, the paid part in a step
# that can be measured. Every run now prints uw.spent(); read it before the
# next raise instead of estimating from here.
#
#   per scan  = 1 (flow feed) + FINVIZ_LOOKUPS + 3 x CANDIDATES  (+ risk calls)
#   per day   = that x ~39 scans, plus the monitor's ~3 per shortlist name
FLOW_ALERT_LIMIT        = int(os.environ.get("FLOW_ALERT_LIMIT") or 500)
# Raised with CORE_TICKERS: the core names take their slots first, and at 60
# they would have eaten half the scan, squeezing out the surprises the flow
# feed exists to find. Measured cost at 80: ~390 UW requests a scan, ~15,000 a
# day with the monitor, against a 30,000 allowance. Read uw.spent().
MAX_CANDIDATES_PER_SCAN = int(os.environ.get("MAX_CANDIDATES_PER_SCAN") or 80)
# Measured on 50 live UW flow alerts (2026-09-08): $250k dropped 25 of 34
# tickers before a single data call, and the scan was nowhere near its
# 60-candidate ceiling — so the filter was discarding names the budget could
# comfortably afford. Cost at the new level: roughly 9,000 UW requests a day
# against a 30,000 allowance. Read uw.spent() before moving it again.
MIN_TICKER_PREMIUM      = 100_000   # skip tickers below this daily premium

# ── Names that are ALWAYS looked at ─────────────────────────────
# Discovery is flow-driven, and UW's flow-alert feed lists what is UNUSUAL.
# A mega-cap trading its normal enormous volume is never unusual, so it never
# gets flagged — and Finviz's movers screen misses it too, because 2% on NVDA
# is not a mover. The 2026-09-08 13:00 scan proves it: 60 tickers evaluated,
# and NVDA, TSLA, AAPL, MSFT, MU, AMD, SPY and QQQ were in none of them.
#
# That day NVDA fell 233.24 -> 225.81 (its 225 put 0.25 -> 1.60) and TSLA ran
# 357.25 -> 370.00 (its 370 call 1.09 -> 3.75). Neither was ever looked at.
#
# This list does NOT replace flow discovery — the surprises Salem asked for
# ("اريد ايضا المفاجات مثلا اوبر") still come from the feed and from Finviz.
# It guarantees the big names are never simply absent.
#
# Cost: one flow lookup each for the ones the feed did not already carry, then
# the usual per-candidate calls. Roughly 4,000-6,000 UW requests a day on top
# of the current ~12,000, against a 30,000 allowance. Read uw.spent().
# ── THE WATCHLIST — the only names this system trades ───────────
# Salem, 2026-09-09, replacing everything that came before it:
#   "١- فقط الشركات التي بالصورة
#    ٢- فقط راقب هذه الشركات والتدفقات على عقودها وان كان هنالك اختراق مقاومة
#       او كسر دعم مع سيولة في السهم و العقود على فريم ١٥ دقيقة ترسل لي افضل
#       ثلاث عقود حسب ميزانيتي"
#
# Eleven names, from the two screenshots. Discovery is OFF: no market-wide
# flow feed deciding the universe, no Finviz movers. These names and nothing
# else, watched all session.
#
# What that buys: every one of them is deeply liquid, has same-day expiries and
# penny-wide books, and is a name he can see moving on his own screen. The
# scanner spent the whole of 2026-09-08 on GH, LYTE, TIGO, DYN and IONS.
WATCHLIST = [t.strip().upper() for t in (os.environ.get("WATCHLIST") or
    "MU,TSLA,AMZN,GOOGL,AAPL,INTC,NVDA,QQQ,META,MSFT,F").split(",") if t.strip()]
# Off, and discovery comes back: the flow feed and Finviz choose the universe
# again and WATCHLIST becomes a guaranteed core inside it.
WATCHLIST_ONLY = os.environ.get("WATCHLIST_ONLY", "1").lower() in ("1", "true", "yes")
CORE_TICKERS = WATCHLIST

# ── Finviz Elite (candidate discovery only — never scored) ──────
# Finviz costs nothing per ticker: one screener request returns every row, and
# the subscription is flat. Only the UW lookups that follow are metered, which
# is why MOVERS can be raised freely and LOOKUPS is the number that matters.
MAX_FINVIZ_MOVERS  = int(os.environ.get("MAX_FINVIZ_MOVERS") or 100)
MAX_FINVIZ_LOOKUPS = int(os.environ.get("MAX_FINVIZ_LOOKUPS") or 40)

# ── Risk checks (risk.py) — deductions only, never bonuses ──────
# These exist because a summed score cannot see a setup that is internally
# incoherent. Each penalty is subtracted after scoring and named in the alert.
EARNINGS_BLOCK_DAYS  = 3     # earnings this close: full penalty
EARNINGS_PENALTY     = 15.0
REGIME_TICKER        = "SPY"
REGIME_MOVE_PCT      = 1.0   # broad-market move that counts as a real tide
REGIME_PENALTY       = 8.0
# Half of 0.55. At 0.28 a tape that is 72% SOLD still passes as buying, so a
# bearish flow can carry a call setup through. Of everything halved here this
# is the one that can turn a real signal upside down — put it back first.
MIN_ASK_SIDE_RATIO   = 0.28  # below this the premium was mostly sold, not bought
# The advisor keeps the ORIGINAL 0.55, deliberately. MIN_ASK_SIDE_RATIO is an
# ENTRY gate — halving it lets more setups through, which is what was asked.
# This one is an EXIT warning on a position Salem is already holding, and
# halving that would silence the warning instead of opening a door. Loosening
# the way in must never quiet the way out.
ADVISOR_PRESSURE_FLOOR = 0.55
MIN_DIRECTIONAL_SHARE = 0.30 # half of 0.60; below this the volume is mostly spread legs, which
                             # say nothing about direction — a live screen found
                             # a contract at 98% multi-leg that would have scored
                             # as a huge one-way bet
CONVICTION_PENALTY   = 10.0
MAX_RISK_PENALTY     = 20.0  # a setup is rejected on its own merits, not buried

# ── Message composition ─────────────────────────────────────────
# True  = `claude -p` writes the Arabic message (Salem's original design)
# False = deterministic Python formatter (no LLM, zero fabrication risk)
# Either way every number comes from the computed JSON.
# Overridable per-environment: a Railway container has no `claude` CLI, so set
# USE_CLAUDE_COMPOSER=0 there.
USE_CLAUDE_COMPOSER = os.environ.get("USE_CLAUDE_COMPOSER", "1").lower() not in ("0", "false", "no")
CLAUDE_TIMEOUT_SEC  = 300

# ── Scheduler (used by scheduler.py on an always-on host) ───────
# Discovery runs on the same clock as the bars it judges. 15m candles close at
# :00/:15/:30/:45, so a 30-minute scan only ever evaluated half of them for
# tickers not yet on the shortlist — a ticker whose flow started at :32 and
# broke at :45 was not looked at until the next hour.
#
# Salem asked for 10, in his words: "انا اريده ان يمسح كل 10 دقائق يضع قوائم
# مراقبة يراقبها على شمعة 15 دقيقة". That is not the same clock as the bars,
# and deliberately so: at 15 the scan and the bar close land together, so a
# name that only starts looking interesting mid-bar waits a full bar to be
# discovered. At 10 the passes fall at :00/:10/:20/:30/:40/:50, which puts a
# discovery pass inside every 15m bar as well as on its close.
# Costs ~4,700 UW requests a day against a 30,000 allowance.
SCAN_EVERY_MIN    = 10
MONITOR_EVERY_MIN = 5
HEARTBEAT_MIN     = 60      # a line in the log so a healthy idle service is
                            # distinguishable from a dead one over a weekend

# ── Analyst layer (analyst.py) ──────────────────────────────────
# A final read on a setup before it is recommended. Requires an Anthropic API
# key — a Pro/Max subscription is for interactive use and cannot authenticate a
# container. Roughly $3.30/month at 5 alerts a day on Opus 5.
# Off by default: without backtest.json its conviction has nothing to anchor to.
USE_ANALYST    = os.environ.get("USE_ANALYST", "0").lower() in ("1", "true", "yes")
ANALYST_MODEL  = os.environ.get("ANALYST_MODEL", "claude-opus-5")
ANALYST_EFFORT = os.environ.get("ANALYST_EFFORT", "high")
# A SKIP verdict removes the alert entirely. Off means the analyst's read is
# attached to the message but Salem still sees the setup.
ANALYST_CAN_BLOCK = os.environ.get("ANALYST_CAN_BLOCK", "1").lower() in ("1", "true", "yes")

# ── Backtest / base rates ───────────────────────────────────────
BASE_RATE_MIN_SAMPLE = 20
REPLICATION_MARGIN   = 0.10 # a feature must beat its own run's average by this
                            # much, on every screen, to count as replicating
WIDE_SPREAD_PCT      = 30   # past this the quote is a placeholder, not a book.
                            # A mid fill inside a 50%-wide spread is the same
                            # fiction as a mid on a penny contract, and it put
                            # spread=50%+ at the top of three out-of-sample runs.
SIDE_EDGE_MARGIN     = 0.10 # a side must clear the stake by this much to count
                            # as paying. $1.035 on 11 contracts is not an edge,
                            # it is a failure to lose.
MIN_CONTRACTS        = 10   # distinct contracts below which a bucket is a
                            # handful of events, not a rate — entry days on one
                            # contract share almost all of their forward window   # a setup type below this is not a base rate, it is
                            # an anecdote — the analyst is told so explicitly

# ── State / data files ──────────────────────────────────────────
DATA_DIR.mkdir(parents=True, exist_ok=True)
SHORTLIST_FILE = DATA_DIR / "shortlist.json"
POSITIONS_FILE = DATA_DIR / "positions.json"
STATE_FILE     = DATA_DIR / "state.json"
LOCK_FILE      = DATA_DIR / ".state.lock"
JOURNAL_FILE   = DATA_DIR / "journal.csv"
BACKTEST_FILE  = DATA_DIR / "backtest.json"
EXPLOSION_FILE = DATA_DIR / "explosion.json"
