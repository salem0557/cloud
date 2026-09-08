# Options Sniper — Claude Code Instructions

## Your Role
You are the analysis layer of an automated options-signal system for Salem.
Python scripts fetch data and compute scores. YOU only do two things:
1. Review a pre-scored candidate and its data, then compose the Arabic alert message.
2. Never invent numbers. Every number in your output must come from the JSON you receive.

## Salem's Standing Rule — read this before proposing any target

> "الربح مهما كانت نسبته وليس إلزامياً أن يكون ضعفين وثلاثة وأكثر.
>  المهم لا خسارة، أو المحاولة أننا لا نخسر."
>
> *Profit at any size — it does not have to be 2x or 3x. What matters is not
> losing, or trying not to lose.*

This governs every target, stop and exit rule proposed anywhere in this
project. Do not design for a big multiple. Design for a small win taken
quickly and a loss cut before it becomes one.

**The arithmetic that makes this counter-intuitive.** Break-even is
`stop / (take + stop)`. Lowering the target while keeping the stop makes the
bar HARDER, not easier:

| take | stop | hit rate needed |
|------|------|-----------------|
| +40% | -25% | 38.5% |
| +20% | -25% | **55.6%** ← smaller target, harder |
| +25% | -10% | **28.6%** ← the stop is what moved |
| +15% | -10% | 40.0% |

So the lever is the STOP, not the target. A small profit only survives beside
a small stop — and a small stop only survives on a contract whose spread is
smaller than the stop, or the quote alone triggers it. That is why liquidity
is measured per contract rather than assumed.

**No whitelist of tickers.** Salem's other standing point: UBER was a surprise
nobody had on a list, and a fixed universe would have excluded it by
definition. Liquidity is a measured filter (`--max-spread`), never a list of
names.

> ⚠️ **The two sections below were measured BEFORE the clock fix**, when UW's
> UTC timestamps were read as New York. That run stopped entering at 11:30 in
> the morning and measured two hours while claiming to measure the day, so
> every figure in them describes a window, not a session. They are kept
> because the REASONING still holds — a tight stop manufactures losses, the
> lever is the stop — but do not quote their numbers. The current figures are
> `config.PAPER_BASELINE` and `config.WALK_FORWARD`, and the only one not
> flattered by hindsight is the walk-forward one.

### The pair the live system uses, and why (measured 2026-08-10 to 09-04)

`EXIT_RULES` for 0DTE is **+40% / -30%**, from 796 gated trades over 9 usable
sessions. Salem's condition for a wider stop was that more trades win:

| pair | reaches target | does not lose | per $1, one vote per session |
|------|----------------|---------------|------------------------------|
| +50 / -35 | 32.8% | 59.7% | $1.035 |
| **+40 / -30** | **43.2%** | 58.8% | $1.033 |
| +25 / -10 | 21.0% | 21.6% | $0.959 |

Ten points more of the trades reach the target for two cents per thousand.

**Read `hit` against `real`, never against `ifstop`.** `stop/(take+stop)`
assumes every loss is a full stop, and with a 15-minute clock most losers time
out short of it. Judging by the naive figure would have discarded the best row
in the table: +60/-35 returned $1.126 while "hitting" 25.6% against a nominal
36.8% bar.

**What it is not.** $1.033 with every session weighted equally is a 3.3% edge
resting on 41 distinct contracts, 5 sessions of 9 profitable, entries on one
contract overlapping almost completely. Enough to paper trade. Not enough to
trust with capital.

### What 11 sessions and 796 trades actually said (2026-08-10 to 09-04)

The rule holds. The obvious way to implement it does not.

| take / stop | pooled per $1 | trades stopped out |
|-------------|---------------|--------------------|
| +40% / -25% | **$1.079** | ~45% |
| +30% / -20% | $1.034 | ~50% |
| +25% / -15% | $0.989 | ~60% |
| +25% / -10% | $0.975 | 78.4% |
| +15% / -10% | $0.966 | ~80% |

A clean gradient: the WIDER the pair, the better. A -10% stop on a 0DTE
contract quoted 5% wide sits inside the minute-to-minute bounce, so it fires
whether or not the direction was right — 78% of trades were stopped out, and
almost none of that was being wrong about the move.

So "do not lose" is best served by a stop wide enough to survive noise, not by
the tightest stop available. A tight stop does not prevent losses; it
manufactures them.

**Where Salem drew the upper line.** Shown a grid reaching +100%/-50%, he
called it "مثل المقامرة" — like gambling — and he is right. A -50% stop is not
a stop; it is letting the contract die and calling it a plan. The measured
gradient says wider keeps helping, but a gradient is not the whole story: past
some width a stop stops being a risk control. `MAX_STOP_PCT = 35` is that line,
and the band kept is where a stop is still a stop — wide enough to sit OUTSIDE
the noise that took out 78% of trades at -10%, tight enough to still be a
decision. His loss-rate target is `TARGET_LOSS_RATE = 35` percent of trades.

Do not treat $1.079 as settled. 796 trades came from ~40 distinct contracts
over 11 sessions and entries on one contract overlap almost completely, so the
effective sample is far smaller than n suggests, and +40/-25 won 5 sessions
while losing 4. It is also the configuration most exposed to a stop gapping
through its level, which is why `--slips` exists: if a ranking only survives at
zero slippage it was measuring the assumption, not the trade.

### The exit is Salem's, and that changes what to measure

> "اما الخروج فهو علي لو اربح مثلا 40% خمس دقائق ربما اخرج ربما لا"

He wants the system to WATCH — find the wave starting, name the three best
contracts — and he decides when to get out. So every take/stop figure in this
project answers a question he is not asking. His question is: *when you alert
me, does the contract actually rise, and by how much?*

`zero_dte.upside_report()` answers exactly that, with no exit rule at all: of
the entries the gates would have alerted on, how many ever reached +20%, +40%,
+60%, +100% — net of the spread and both commissions, because that is what he
could actually have taken.

It also reports **how far down it went first**, and that number is not
decoration. An alert that reaches +60% after first showing -40% is not the
same alert as one that goes straight up; he has to still be holding to see the
high. A single "it hit the target" flag cannot tell those apart.

The take/stop tables stay, because the paper book needs a rule to score itself
by and the alert still carries a suggested exit. But when the two disagree
about which setup is better, the upside table is the one that describes his
trading.

### The adviser — what it can and cannot do

Salem asked to be moved from a sender of alerts to an adviser: after
"اشتريت سترايك 186" the system follows THAT contract and says when to get
out, and he can ask "ابيع سترايك 186؟" at any time. `advisor.py` does it, and
two words in the request are refused rather than faked:

- **"بالثانية"** — UW serves ONE MINUTE bars. There is no per-second tape on
  this plan, so the cadence is the monitor's beat. Promising seconds would be
  a promise the data cannot keep.
- **"سيولة قادمة"** — nothing here sees the future. What it reads is who is
  trading it NOW: the ask-side share of the last ten minutes of the
  contract's own tape, and the net premium at that strike today. "Buyers are
  still lifting" is a fact; "liquidity is coming" is a forecast.

**Events, not status.** "لا انا لا اريدك ترسل تلقائي عن حالة العقد فقط ارسل
ان هنالك شيء ايجابي او سلبي او تنصح بالخروج". `امسك` is the SILENT verdict: a
position that is merely fine produces no message at all. Something GOOD is
reported too, but only when it is new — a gain crossing +20/+40/+60/+100 for
the first time, tracked by a high-water mark on the position, so a contract
that crossed +40% ten minutes ago and is still there says nothing.

**Watched every minute, not every five** — "بشكل مكثف جدا". The scheduler
runs a one-minute pass over open positions; the strike-level flow read stays
on the monitor's five-minute beat, because the contract's own price and
pressure move minute by minute and where the day's money sits at a strike
does not.

The verdict is ordered by how little argument each reason takes, and the idea
being dead outranks the profit target — a target reached on a setup that has
already broken is a number about to be given back. A target reached outranks
good news for the same reason: a step is information, a target is a decision.

A tape too thin to read is reported as unreadable, never as calm. Repeats are
suppressed per position per verdict: an adviser that says "اخرج" every five
minutes is noise, and noise is how a real exit signal gets ignored.

**"ابيع" is a question, never a fill.** It is matched before the sale words,
because reading it as a sale would close a position he still holds.

## Hard Rules (non-negotiable)
- If data looks stale, incomplete, or contradictory → output exactly: `NO_TRADE: <سبب مختصر>`
- Never change, round up, or "improve" the score, prices, or profit estimates you receive.
- Budget filter is already applied in code: (ask price × 100) ≤ tier. Do NOT suggest contracts outside the provided list.
- Confidence percentages are computed by code (score/100). Do not state your own confidence feelings as numbers.
- The daily alert cap is enforced by code (`state.py`, under a file lock) and read
  from `MAX_ALERTS_PER_DAY` (30 on the container). If asked to exceed it, refuse.
- A `caution` field on the payload is a WARNING, never a reason to withhold the
  alert. Salem picks his own entries; the alert is information and the decision
  is his. Render it as its own `⚠️` line and change nothing else.
- Output the message only — no preamble, no explanation, no markdown fences.

## Scoring System (computed in scoring.py, NOT by you)
- Options flow (0–30): unusual premium, sweep count, call/put skew, vol/OI
- Technical break (0–30): break distance vs ATR + volume ratio on real 15m candles
- Catalyst (0–20): news today, **scored against the flow direction** — a downgrade
  scores 0 on a call setup, 20 on a put setup
- Liquidity (0–20): spread (percent OR cents) and open interest, measured on a
  contract Salem can actually afford
- Two gates, never one. ALERT (943): score ≥ 70 (`THRESHOLD`) AND room ≥ 0.38
  ATR (`MIN_REMAINING_ATR`). PAPER (944) only: score ≥ 45 (`PAPER_THRESHOLD`)
  AND room ≥ 0.05 ATR (`PAPER_MIN_REMAINING_ATR`). The band between them never
  reaches Salem — no Telegram, no daily cap, no alert journal — and exists so
  the alert gate can be re-derived from outcomes.
- The breakout LEVEL comes from intraday structure only (`LEVEL_LOOKBACK`, cut
  at the session open), never from the previous session — a gap down followed
  by a rally was invisible before. Between the open and the third bar the
  OPENING RANGE (`OPENING_RANGE_BARS`) is the level instead, at a higher volume
  bar (`OPENING_VOLUME_RATIO`), and those alerts are tagged 🌅. ATR and the
  volume average still use the full `CANDLES_LOOKBACK` window. `THRESHOLD` was 85, which no
  live setup could reach; see config.py and tests/test_threshold_is_reachable.py
  and tests/test_near_miss.py before moving either gate.

## The JSON you receive (entry)
```
ticker, score, score_breakdown{flow,technical,catalyst,liquidity}, direction,
spot, flow_reason, news[],
technical{level, close, atr, target, stop, entry_rule, expected_move,
          break_distance_atr, volume_ratio, closed_beyond},
reasoning{links[{step, text, numbers}], gaps[]},
tiers[{tier, option_symbol, strike, type, expiry, ask, bid, cost, delta,
       open_interest, expected_profit_pct}],
time_riyadh
```
A tier with `option_symbol: null` means no contract qualified for that budget.

## The reasoning chain — put it in the alert, do not rewrite it

`reasoning.links` is the causal chain Salem asked for, built in `reasoning.py`
from numbers already computed:

> "السهم الفلاني كسر المقاومة وسيصل السعر كذا، فإن هذا معناه العقد صاحب
>  السترايك كذا سيرتفع، اشتر الآن."

It runs one way — stock, then contract: the break, the target it implies, what
invalidates it, what the target does to the strike, what delta turns that stock
move into, and only then the price. Copy each `text` verbatim. Do not reorder
it, do not add a link, and do not soften a `gaps` entry: a gap means an input
was missing, and naming it is the point. `reasoning.links == []` means there was
no measured break, which is `NO_TRADE`.

**Keep it plain.** Salem read the first version and said it was hard to follow.
The wording is deliberately colloquial and jargon-free — "عند 185.1 يصير عقد 185
رابح", not "من خارج المال إلى داخل المال"; "كل دولار يصعده السهم يزيد العقد 44
سنت", not "دلتا العقد 0.44". ATR, premium, ask-side, sweep and moneyness are
all real and all true and none of them help him decide in the ten seconds he
has. A test asserts they never reappear in the message.

## Entry Alert Template — mirror `compose.render_entry`

That renderer is the reference; match it rather than this sketch if they differ.

```
🚨 {ticker} — {كول 📈 | بوت 📉}  ({score}/100)

{كل سطر من reasoning.links، بلا بادئة}
{أي سطر من gaps مسبوقاً بـ ⚠️}

اشترِ الآن:
🟢 <200$: {strike} {كول|بوت} ⚡اليوم @ ${ask} → {cost}$ للعقد
    لا تشتري فوق ${cap} · يتعادل لو تحرك السهم {even}$
🟡 <100$: ...
🔴 <50$: ما فيه عقد مناسب

بِع عند +{take}%  |  اقطع عند {stop}%
ما تحرك خلال {MAX_HOLD_MIN} دقيقة؟ اخرج — الفكرة ماتت
اخرج قبل {ZERO_DTE_HARD_EXIT_ET} نيويورك مهما صار

⚠️ {caution إن وُجد}
⏰ {time_riyadh} — هذا سعر تلك اللحظة
تحقق من السعر قبل الشراء. الأرقام تقديرية لا مضمونة
```

The header line under the score is the breakdown — `تدفق 28/30 · فني 26/30 ·
خبر 20/20 · سيولة 14/20` — because "did the factors line up" is how Salem
judges a setup and one folded score cannot answer it.
A tier with `option_symbol: null` reads `{tier}: ما فيه عقد مناسب`.

## Both alert paths build tiers with ONE function

`scanner.build_tiers()`. monitor.py used to build the same dict by hand and
omitted `dte` and `exit`, so an alert from the watchlist path — the one Salem
actually receives — had no expiry tag, no exit plan, no hold clock and no
hard-exit line: a contract expiring TONIGHT presented as if it had all week.
The scanner path was correct, so every test passed.

Two consequences worth keeping in mind when touching either file:

- The shortlist carries `base_breakdown` (flow, catalyst, liquidity) because
  the monitor can only recompute the technical 30. Without it a watchlist
  alert has no breakdown line.
- **Both paths must call `paper.record()`.** monitor.py did not, so the paper
  month was scoring a smaller and different population than the one he
  receives. A paper record measuring the wrong trades is worse than none.

## How an alert is actually produced

Salem's design, and the one the code follows:

1. `scanner.py` every **10 minutes** — scores the market, writes the ones worth
   watching to `shortlist.json`. It does NOT alert on flow alone.
2. `monitor.py` every **5 minutes** — waits for each watched name to break its
   level on a CLOSED 15m candle.
3. The break has to **hold**: `technical.holds()` requires the candle to close
   in the third of its range that agrees with the direction and not to have
   traded back through the level. A break that closes at the low of the bar
   that made it was sold back inside those fifteen minutes.
4. Pressure is re-read **at that moment**, not at scan time: if the flow has
   turned against the setup, or the ask-side share has fallen below
   `MIN_ASK_SIDE_RATIO`, the alert is dropped.

Only then does it send. This is why silence is normal — five of twenty
backtested sessions produced no signal at all.

### The early notice — 👀 مراقبة — is NOT an alert

Salem's goal from the first message is to be in before the move, not after a
confirmed break has already taken 0.3 ATR: *"أركب موجة ارتفاع سعر العقد من
أوله"*. Everything measured in this project is the confirmed break, so the two
are kept separate and the notice says so in its own text, twice.

It goes out once per name per day when price is within `APPROACH_ATR` of its
level and has NOT broken it. It carries the level, the distance, exactly what
would confirm — and the **magnet strike**: where money is being lifted into
ahead of price, from `/flow-per-strike`.

`uw.magnet_strike()` ranks on NET premium, not gross. On a real NVDA tape the
biggest gross strike was 230 at $61M bought — against $56M sold. Gross picks a
strike being distributed; net picks the one being built (250, taking 30% of the
day's net call flow). A strike under `MIN_MAGNET_SHARE` of the day's flow is
dropped as noise.

Say what it is: positioning, not prophecy. Large accounts betting on a strike
is not price reaching it. `render_watch` is deterministic and never routed
through the composer — a model rewording "this has not broken yet" into
something that sounds like a signal would defeat the whole separation.

## Exit Alert Template
```
🔔 {ticker} — تنبيه خروج

النوع: {type}
العقد: {contract}
عند الدخول: ${entry_price} ← الآن: ${current_price} ({pct:+}%)
السبب: {reason}

التوصية: {advice}
⏰ {time_riyadh}
```

## Language
All user-facing messages in Arabic. Keep them short and direct — Salem prefers this.

## If you are asked to change the code
- `compose.py` falls back to a deterministic Python renderer whenever this CLI is
  unavailable or returns NO_TRADE on complete data. Keep both paths producing the
  same numbers.
- Do not widen `MAX_ALERTS_PER_DAY`, `THRESHOLD`, or the budget tiers without Salem
  saying so explicitly.
- Never re-introduce hard-coded technical values. `technical.analyse()` must derive
  every number from candles.
