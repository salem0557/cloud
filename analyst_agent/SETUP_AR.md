# التشغيل خطوة بخطوة — وين أضع كل شيء

هذا الملف يجاوب سؤالاً واحداً: **وين أكتب كل كلمة**. اتبعه بالترتيب.

الخطوات 1-4 تُنفَّذ **مرة واحدة على جهازك** (لأن تلقرام يرسل كود تحقق لازم تكتبه
بيدك)، ثم الخطوة 5 تنقل الوكيل إلى Railway ليعمل 24 ساعة.

---

## 1) جهّز جهازك (مرة واحدة)

**ويندوز:**
1. نزّل بايثون من [python.org/downloads](https://www.python.org/downloads/) —
   وفي أول شاشة التثبيت علّم ✅ على **Add Python to PATH**.
2. افتح **PowerShell** (زر ويندوز ← اكتب `powershell` ← إنتر).

**ماك:** افتح **Terminal** (Command + مسافة ← اكتب `terminal`). بايثون موجود أصلاً.

ثم نزّل المشروع وثبّت المكتبات — انسخ الأسطر التالية سطراً سطراً:

```bash
git clone -b claude/affectionate-bell-9rszd0 https://github.com/salem0557/cloud.git
cd cloud
pip install -r requirements.txt
```

> لو `git` غير مثبّت: افتح صفحة البرانش على GitHub ← زر **Code** ← **Download ZIP**،
> فك الضغط، وافتح المجلد من الطرفية بأمر `cd` إلى مساره.

---

## 2) مفتاح Groq

1. افتح [console.groq.com/keys](https://console.groq.com/keys) وسجّل دخول.
2. **Create API Key** ← انسخ المفتاح (يبدأ بـ `gsk_`).

---

## 3) api_id و api_hash من تلقرام

1. افتح [my.telegram.org](https://my.telegram.org) وسجّل برقم جوالك (يوصلك كود في تلقرام).
2. اختر **API development tools**.
3. لو طلب بيانات تطبيق: App title و Short name أي اسم (مثل `analyst`)، والباقي اتركه.
4. انسخ **api_id** (أرقام) و **api_hash** (حروف وأرقام).

---

## 4) ملف `.env` — وين تضع الكلام

داخل مجلد المشروع `cloud` أنشئ ملفاً اسمه **`.env`** بالضبط (بنقطة في أوله وبلا أي امتداد).

أسهل طريقة، من نفس الطرفية داخل مجلد `cloud`:

```bash
# ويندوز PowerShell
notepad .env

# ماك
nano .env
```

الصق فيه هذه الأسطر الأربعة وضع قيمك مكان الـ xxx:

```env
GROQ_API_KEY=gsk_ضع_مفتاح_groq_هنا
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=ضع_الهاش_هنا
TELEGRAM_SESSION=
```

احفظ وأغلق (في notepad: Ctrl+S ثم أغلق — في nano: Ctrl+O ثم إنتر ثم Ctrl+X).

> لا شيء آخر مطلوب. كل المتغيرات الأخرى في `.env.example` اختيارية ومعلّقة بـ `#`.

### الآن استخرج سطر الدخول (session)

```bash
python -m analyst_agent.login
```

يسألك:
* **phone number** → رقم جوال حساب الوكيل بصيغة دولية، مثل `+9665xxxxxxxx`
* **code** → الكود الذي وصل لتلقرام
* **password** → فقط إذا كان الحساب عليه تحقق بخطوتين

سيطبع سطراً طويلاً يبدأ بـ:

```
TELEGRAM_SESSION=1BQANOTEuMTA4...
```

**انسخه كاملاً** وافتح `.env` مرة أخرى واستبدل به السطر الفارغ `TELEGRAM_SESSION=`.

> ⚠️ هذا السطر = دخول كامل لحساب تلقرام. لا ترسله لأحد ولا تضعه في قروب.
> والأفضل أن يكون حساب تلقرام منفصلاً عن حسابك الشخصي.

### جرّبه على جهازك قبل النشر

```bash
python -m analyst_agent.cli NVDA 15m
python -m analyst_agent.userbot
```

الأمر الثاني يشغّل الوكيل. أضف حساب الوكيل إلى قروبك، وأرسل في القروب:
«حلل NVDA فريم 15 دقيقة». لإيقافه: Ctrl+C.

---

## 5) النشر على Railway (ليعمل بدون جهازك)

1. ارفع التغييرات لو عدّلت شيئاً، أو استخدم البرانش كما هو.
2. في [railway.app](https://railway.app) داخل مشروعك: **New** ← **GitHub Repo** ←
   اختر `salem0557/cloud`. (خدمة ثانية مستقلة عن خدمة `bot.py` الحالية.)
3. في الخدمة الجديدة ← **Settings**:
   * **Branch**: `claude/affectionate-bell-9rszd0`
   * **Start Command**: `python -m analyst_agent.userbot`
4. ← **Variables** ← **New Variable** وأضف الأربعة بنفس الأسماء والقيم التي في `.env`:

| Name | Value |
|---|---|
| `GROQ_API_KEY` | `gsk_...` |
| `TELEGRAM_API_ID` | الأرقام |
| `TELEGRAM_API_HASH` | الهاش |
| `TELEGRAM_SESSION` | السطر الطويل من الخطوة 4 |

5. **Deploy**. راقب **Logs**، ولما تشوف:

```
logged in as ... | analyst userbot is running — waiting for charts
```

فالوكيل شغّال. جرّب من القروب مباشرة.

> ملف `.env` لا يُرفع إلى Railway ولا إلى GitHub (محجوب في `.gitignore`) —
> ولهذا تُكتب القيم في **Variables** هناك.

---

## 6) الاستخدام اليومي

| ترسل في القروب | النتيجة |
|---|---|
| صورة تشارت + «حلل 15 دقيقة» | تشارت بالمؤشرات + قراءة فنية وخطة |
| «حلل NVDA يومي» (بلا صورة) | نفس الشيء، يجيب البيانات بنفسه |
| رد على صورة قديمة بكلمة «حلل» | يحلل تلك الصورة |
| `/help` | شرح مختصر |
| `/ping` | يتأكد أنه شغّال |

---

## لو صار خطأ

| الرسالة | الحل |
|---|---|
| `ضع TELEGRAM_API_ID و TELEGRAM_API_HASH` | الملف `.env` غير موجود أو الأسماء غلط — راجع الخطوة 4 |
| `GROQ_API_KEY غير مضبوط` | مفتاح Groq ناقص، أو نُسخ بمسافة زائدة |
| «ما عرفت الرمز» | اكتب الرمز صريحاً: `NVDA` أو `$NVDA` |
| «خارج السوق الذي أنا مضبوط عليه» | الرمز غير أمريكي — الوكيل مضبوط على السوق الأمريكي |
| الوكيل ساكت في القروب | اكتب كلمة «حلل» أو اعمل منشن له أو رد على رسالته |
| `python` غير معروف في ويندوز | لم تُعلّم **Add Python to PATH** — أعد التثبيت |
