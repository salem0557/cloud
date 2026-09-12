# التشغيل خطوة بخطوة

فيه طريقتان. **ابدأ بالأولى** — أسهل بكثير ولا تحتاج جهازك ولا أي تسجيل دخول.

| | الطريقة (أ): بوت من BotFather | الطريقة (ب): يوزر بوت |
|---|---|---|
| يحتاج تثبيت بايثون على جهازك | ❌ لا | ✅ نعم (مرة واحدة) |
| يحتاج كود تحقق من تلقرام | ❌ لا | ✅ نعم |
| الوقت | ~5 دقائق | ~20 دقيقة |
| شكله في القروب | بوت باسمه الخاص | حساب عادي |
| التحليل | **نفسه تماماً** | نفسه تماماً |

---

# الطريقة (أ) — بوت من BotFather (المستحسن)

كل شيء من جوالك ومن موقع Railway. ما تحتاج تفتح طرفية ولا تنزّل بايثون.

## 1) أنشئ البوت (من تلقرام، دقيقتان)

1. افتح تلقرام وابحث عن **@BotFather**.
2. أرسل `/newbot`.
3. يسألك عن الاسم → اكتب أي اسم، مثل `محلل التشارت`.
4. يسألك عن اليوزر → لازم ينتهي بـ `bot`، مثل `salem_analyst_bot`.
5. يرسل لك سطراً مثل:
   `7712345678:AAH8s-xxxxxxxxxxxxxxxxxxxxxxxxxxxx`
   **هذا هو التوكن — انسخه.**

> ⚠️ لا تستخدم توكن بوت الماسح القديم. بوتان على توكن واحد يتعارضان ولا يعمل أيٌّ منهما.

## 2) اسمح له يقرأ الرسائل في القروب (مهم جداً)

في نفس محادثة **@BotFather**:

1. أرسل `/setprivacy`
2. اختر بوتك من القائمة
3. اضغط **Disable**

بدون هذه الخطوة، البوت في القروب لن يرى كلمة «حلل» — سيرى فقط الأوامر التي تبدأ
بـ `/` والمنشن والردود عليه.

## 3) مفتاح Groq (دقيقتان)

1. افتح [console.groq.com/keys](https://console.groq.com/keys) وسجّل دخول بـ Google.
2. **Create API Key** ← انسخ المفتاح (يبدأ بـ `gsk_`).

## 4) شغّله على Railway (لا يحتاج جهازك)

1. في [railway.app](https://railway.app) داخل مشروعك: **New** ← **GitHub Repo** ←
   اختر `salem0557/cloud`. (خدمة جديدة مستقلة عن خدمة `bot.py`.)
2. **Settings**:
   * **Branch**: `claude/affectionate-bell-9rszd0`
   * **Start Command**: `python -m analyst_agent.telebot`
3. **Variables** ← **New Variable** ← أضف اثنين فقط:

| Name | Value |
|---|---|
| `ANALYST_BOT_TOKEN` | التوكن من الخطوة 1 |
| `GROQ_API_KEY` | مفتاح Groq من الخطوة 3 |

4. **Deploy**، وافتح **Logs**. لما تشوف:

```
analyst bot is running — waiting for charts
```

فالبوت شغّال.

## 5) أضفه للقروب وجرّب

1. في تلقرام ابحث عن يوزر بوتك ← **Add to Group**.
2. أرسل في القروب: `/ping` → يرد «شغّال ✅».
3. أرسل صورة تشارت واكتب معها: «حلل NVDA فريم 15 دقيقة».

خلصت. **ما فتحت طرفية ولا نزّلت شيئاً.**

---

# الطريقة (ب) — يوزر بوت (حساب حقيقي)

استخدمها فقط لو تبي الوكيل يظهر كعضو عادي في القروب لا كبوت. تحتاج تسجيل دخول
مرة واحدة لأن تلقرام يرسل كود تحقق لازم تكتبه بيدك.

## وين تسجّل الدخول — ثلاثة خيارات

**الخيار 1: من المتصفح بلا أي تثبيت (Google Colab)**

1. افتح [colab.research.google.com](https://colab.research.google.com) ← **New notebook**.
2. الصق في الخلية الأولى واضغط زر التشغيل:

```python
!pip install -q telethon
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
api_id = int(input("api_id: "))
api_hash = input("api_hash: ")
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("TELEGRAM_SESSION=" + client.session.save())
```

3. سيطلب `api_id` و `api_hash` (من [my.telegram.org](https://my.telegram.org) ←
   API development tools)، ثم رقم الجوال، ثم الكود الذي يوصلك في تلقرام.
4. انسخ السطر الطويل `TELEGRAM_SESSION=1BQANOTEu...`

**الخيار 2: من جهازك** — نزّل بايثون من
[python.org/downloads](https://www.python.org/downloads/) وعلّم ✅ **Add Python to PATH**،
ثم في PowerShell أو Terminal:

```bash
git clone -b claude/affectionate-bell-9rszd0 https://github.com/salem0557/cloud.git
cd cloud
pip install -r requirements.txt
python -m analyst_agent.login
```

**الخيار 3: من جوالك** — تطبيق **Termux** (أندرويد) ثم `pkg install python` ونفس
أوامر الخيار 2.

## ثم النشر

نفس خطوات Railway أعلاه، لكن:
* **Start Command**: `python -m analyst_agent.userbot`
* **Variables**: `GROQ_API_KEY` و `TELEGRAM_API_ID` و `TELEGRAM_API_HASH` و
  `TELEGRAM_SESSION` (السطر الطويل).

> ⚠️ سطر `TELEGRAM_SESSION` = دخول كامل للحساب. لا ترسله لأحد. والأفضل حساب
> تلقرام منفصل عن حسابك الشخصي.

---

# التشغيل على جهازك (اختياري، للتجربة)

لو نزّلت المشروع، أنشئ ملفاً اسمه **`.env`** داخل مجلد `cloud` (بنقطة في أوله)
وضع فيه:

```env
ANALYST_BOT_TOKEN=7712345678:AAH8s-xxxx
GROQ_API_KEY=gsk_xxxx
```

ثم:

```bash
python -m analyst_agent.cli NVDA 15m     # تحليل بلا تلقرام، يحفظ chart.png
python -m analyst_agent.telebot          # تشغيل البوت (إيقاف: Ctrl+C)
```

ملف `.env` يبقى على جهازك ولا يُرفع لـ GitHub ولا لـ Railway — في Railway تُكتب
القيم في **Variables**.

---

# الاستخدام اليومي

**اكتب براحتك مع الصورة** — ما فيه صيغة محددة:

| ترسل في القروب | النتيجة |
|---|---|
| صورة تشارت + أي كلام («وش رايك؟»، «ادخل ولا أنتظر؟»، «على الخمس دقايق») | تشارت بالمؤشرات + قراءة فنية وخطة، ويجاوب سؤالك أولاً |
| صورة تشارت بلا أي كلام | نفس الشيء (الفريم من الصورة، وإلا اليومي) |
| صورة ليست تشارت (ميم، لقطة شاشة) | **يسكت** بلا أي رد |
| نص بلا صورة: «حلل NVDA يومي» | يجيب البيانات بنفسه — هنا يحتاج كلمة «حلل» أو منشن |
| رد على صورة قديمة بأي كلام | يحلل تلك الصورة |
| `/help` | شرح مختصر |
| `/ping` | يتأكد أنه شغّال |

في المحادثة الخاصة كل شيء يعمل بلا كلمة تنبيه، حتى «نفيديا؟».

متغيرات اختيارية مفيدة (في Variables أو `.env`):

| Name | Value | الفائدة |
|---|---|---|
| `ANALYST_OWNER_IDS` | معرّفك الرقمي | يعفيك من فترة الانتظار بين الطلبات |
| `ANALYST_DEFAULT_FRAME` | `1d` | الفريم إن لم يُذكر ولم يظهر في الصورة |
| `ANALYST_ANSWER_ALL_PHOTOS` | `false` | يوقف تحليل الصور تلقائياً (يرجع يطلب كلمة «حلل») |

---

# لو صار خطأ

| الرسالة / الحالة | الحل |
|---|---|
| `ضع ANALYST_BOT_TOKEN في ملف .env` | التوكن ناقص في Variables أو الاسم مكتوب غلط |
| `GROQ_API_KEY غير مضبوط` | مفتاح Groq ناقص أو نُسخ بمسافة زائدة |
| البوت ساكت في القروب مع كلمة «حلل» | لم تُنفّذ خطوة `/setprivacy` ← Disable |
| البوت يرد على `/ping` فقط | نفس السبب أعلاه |
| `Conflict: terminated by other getUpdates` | نفس التوكن يعمل في مكانين — أنشئ بوتاً جديداً |
| «ما عرفت الرمز» | اكتب الرمز صريحاً: `NVDA` أو `$NVDA` |
| «خارج السوق الذي أنا مضبوط عليه» | الرمز غير أمريكي (الوكيل مضبوط على السوق الأمريكي) |
