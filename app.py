from flask import Flask, request, jsonify, render_template
import pandas as pd
import time
import os
import re
from difflib import SequenceMatcher
from dotenv import load_dotenv

try:
    from google import genai
except Exception:
    genai = None

load_dotenv()

app = Flask(__name__)


SHEET_ID = "1eX0HjdZKYD9TvvavRWzL1uQ0sCFv_u_X-38vNholUeA"
CACHE_TIME = 28800  # 8 hours
GEMINI_MODEL = "gemini-2.5-flash"

cache_links = {}
last_update = 0

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
gemini_client = None

if genai is not None and GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        gemini_client = None


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def normalize_text(text: str) -> str:
    if text is None:
        return ""

    text = str(text).strip().lower()

    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = text.replace("،", ",")
    text = re.sub(r"[^\w\s,/-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str):
    text = normalize_text(text)
    return [t for t in text.split() if t]


def looks_like_course_code(text: str) -> bool:
    return bool(re.search(r"\b[a-zA-Z]{2,6}\s*\d{2,4}\b", text or ""))


def load_links():
    global cache_links, last_update

    if cache_links and time.time() - last_update < CACHE_TIME:
        return cache_links

    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
    df = pd.read_csv(url)

    links_dict = {}

    if "keywords" not in df.columns or "link" not in df.columns:
        cache_links = {}
        last_update = time.time()
        return cache_links

    for _, row in df.iterrows():
        if pd.isna(row["keywords"]) or pd.isna(row["link"]):
            continue

        raw_keywords = str(row["keywords"]).split(",")
        link = str(row["link"]).strip()

        if not link:
            continue

        for keyword in raw_keywords:
            clean_keyword = normalize_text(keyword)
            if clean_keyword:
                links_dict[clean_keyword] = link

    cache_links = links_dict
    last_update = time.time()
    return cache_links


def detect_intent(user_message: str) -> str:
    msg = normalize_text(user_message)

    general_patterns = [
        "اسمك", "من انت", "مين انت", "كيفك", "شو بتعمل", "ساعدني",
        "ما معنى", "اشرح", "كيف", "ليش", "لماذا", "what", "who", "how", "why"
    ]

    link_patterns = [
        "رابط", "لينك", "المصدر", "البورتال", "portal",
        "محاضره", "محاضرة", "ماده", "مادة"
    ]

    schedule_patterns = [
        "جدول", "اقترح", "اسجل", "سجل", "خطة", "مواد الفصل"
    ]

    if looks_like_course_code(user_message):
        return "course_like"

    if any(p in msg for p in schedule_patterns):
        return "general_ai"

    if any(p in msg for p in link_patterns):
        return "course_like"

    if any(p in msg for p in general_patterns):
        return "general_ai"

    # إذا كانت الرسالة قصيرة جدًا غالبًا اسم مادة
    if len(msg.split()) <= 3:
        return "course_like"

    return "unknown"



def find_best_link(user_message: str, links: dict):
    normalized_message = normalize_text(user_message)
    message_tokens = set(tokenize(normalized_message))

    best_score = 0.0
    best_link = ""

    for keyword, link in links.items():
        keyword_tokens = set(tokenize(keyword))

        seq_score = similarity(normalized_message, keyword)

        contains_score = 0.0
        if keyword in normalized_message or normalized_message in keyword:
            contains_score = 0.97

        overlap_score = 0.0
        if keyword_tokens:
            overlap_score = len(message_tokens & keyword_tokens) / len(keyword_tokens)

        score = max(seq_score, contains_score, overlap_score)

        if score > best_score:
            best_score = score
            best_link = link

    return best_score, best_link


def ask_gemini(user_message: str) -> str:
    if gemini_client is None:
        return (
            "لم أجد رابطًا مناسبًا، وخدمة الذكاء الاصطناعي غير مفعلة حاليًا.\n"
            "اكتب اسم المادة كما هو في البورتال، أو فعّل GEMINI_API_KEY."
        )

    prompt = f"""
    (mubtaker)اسمك مبتكر 
أنت مساعد أكاديمي عربي مخصص لطلاب قسم علم الحاسوب في الجامعة العربية الأمريكية.
مهمتك:
1) مساعدة الطالب في فهم الخطة الدراسية.
2) الإجابة عن الأسئلة المتعلقة بالمواد، ترتيبها، عدد الساعات، والفصل المناسب.
3) اقتراح جدول دراسي بشكل عام بناءً على الخطة فقط.
4) إذا لم تكن معلومات الطالب كافية، اطلب منه توضيح المواد التي أنهاها أو عدد الساعات التي يريد تسجيلها.
5) لا تخترع معلومات غير موجودة في الخطة.
6) لا تذكر مواد ليست في الخطة.
7) لا تعطِ روابط من عندك.
8) إذا سأل الطالب سؤالًا عامًا لا يعتمد على الخطة، أجب باختصار وبأسلوب لطيف.
9) إذا طلب الطالب اقتراح جدول، فاعتمد على تسلسل الخطة الدراسية، ويفضل اقتراح مواد الفصل الأقرب التالي قبل القفز إلى فصول متقدمة.
10) إذا ذكر الطالب مواد أنهاها، فخذها بعين الاعتبار بشكل منطقي عند الإجابة.

الخطة الدراسية المعتمدة لقسم علم الحاسوب:

السنة الأولى - الفصل الأول:
- 010610014: لغة انجليزية للمبتدئين (0)
- 040111001: اللغة العربية (2)
- 110411000: مهارات الحاسوب (2)
- متطلب جامعي اختياري (2)
- متطلب جامعي اختياري (2)
- 100411010: تفاضل وتكامل - 1 (3)
- 110111030: مختبر مقدمة في تكنولوجيا المعلومات (1)
- 240221010: مقدمة في تكنولوجيا المعلومات (2)
المجموع: 14

السنة الأولى - الفصل الثاني:
- 010610025: لغة إنجليزية للمتوسطين (2)
- 010610026: لغة إنجليزية للمتوسطين مختبر (1)
- 100411020: تفاضل وتكامل - 2 (3)
- 100413750: رياضيات منفصلة (3)
- 240111011: اساسيات البرمجة (++C) (3)
- 240111021: مختبر اساسيات البرمجة 1 (++C) (1)
- 110411100: تصميم المنطق الرقمي (3)
المجموع: 16

السنة الثانية - الفصل الأول:
- 010610035: لغة انجليزية للمتقدمين (2)
- 010610036: لغة انجليزية للمتقدمين مختبر (1)
- 040521301: أسس أساليب البحث (2)
- 100412040: الرياضيات لتكنولوجيا المعلومات (3)
- 110412120: مختبر اساسيات البرمجة 2 (1)
- 240112003: اساسيات البرمجة 2 (3)
- 240112111: مقدمة في هيكلية الحاسوب (3)
- مساقات حرة (3)
المجموع: 18

السنة الثانية - الفصل الثاني:
- 040511011: الدراسات الفلسطينية (2)
- متطلب جامعي اختياري (2)
- 110113220: مختبر شبكات الحاسوب (1)
- 110412130: مختبر تركيب بيانات (1)
- 240112031: تركيب البيانات (3)
- 240113121: مقدمة في قواعد البيانات (3)
- 240113132: مختبر مقدمة في قواعد البيانات (1)
- 240213480: المحادثة والكتابة التقنية (3)
المجموع: 16

السنة الثالثة - الفصل الأول:
- 240113020: تقنيات البرمجة والخوارزميات (3)
- 240113311: مقدمة في نظم التشغيل (3)
- 240212010: مبادئ برمجة الكيانات (3)
- 240213081: تطوير تطبيقات الانترنت 1 (3)
- مساقات حرة (3)
المجموع: 15

السنة الثالثة - الفصل الثاني:
- متطلب جامعي اختياري (2)
- 240113171: مقدمة في هندسة البرمجيات (3)
- 240113291: برمجة الأجهزة المحمولة (3)
- 240114471: إدارة مشاريع تكنولوجيا المعلومات (3)
- 240213010: برمجة الكيانات المتقدمة (3)
- مساقات حرة (3)
المجموع: 17

السنة الثالثة - الفصل الصيفي:
- 000011110: خدمة مجتمع (0)
- 240113990: تدريب ميداني - علم الحاسوب (3)
المجموع: 3

السنة الرابعة - الفصل الأول:
- 240113620: التحقق واختبار البرمجيات (3)
- 240114331: عمارة الحاسوب (3)
- 240114341: مختبر يونكس (1)
- 240114974: مشروع تخرج 1 (1)
- 240212100: أساسيات رسومات الحاسوب (3)
- 240213231: البرمجة المرئية (3)
- متطلب تخصص اختياري (3)
المجموع: 17

السنة الرابعة - الفصل الثاني:
- 240113221: أمن المعلومات (3)
- 240114081: نظرية الحوسبة (3)
- 240114350: الذكاء الإصطناعي (3)
- 240114982: مشروع التخرج 2 - علم الحاسوب (3)
- متطلب تخصص اختياري (3)
- متطلب تخصص اختياري (3)
المجموع: 18

قواعد الرد:
- أجب بالعربية.
- كن واضحًا ومختصرًا ومفيدًا.
- إذا سأل الطالب عن مادة، اذكر اسمها ورقمها وساعاتها إذا كانت موجودة بالخطة.
- إذا سأل عن اقتراح جدول، لا تعطِ جدولًا نهائيًا حاسمًا إذا كانت المعلومات ناقصة، بل أعطه اقتراحًا أوليًا واطلب منه ذكر المواد التي أنهاها.
- إذا كان السؤال خارج الخطة، قل ذلك بوضوح.
-- عند طلب اقتراح جدول:
  1) افترض أن الطالب يريد السير وفق تسلسل الخطة.
  2) لا تقترح مواد من سنة متقدمة قبل استكمال المواد الأساسية في السنوات السابقة إلا إذا ذكر الطالب أنه أنهاها.
  3) إذا لم يذكر الطالب المواد التي أنجزها، فاطلب منه ذلك أولًا أو أعطه اقتراحًا مبدئيًا بناءً على الفصل التالي المنطقي.
  4) إذا ذكر الطالب عدد الساعات، حاول أن يكون الاقتراح قريبًا من هذا العدد.
  5) إذا كانت هناك مواد بدون رقم مساق مثل المتطلبات الاختيارية أو المساقات الحرة، اذكرها بهذا الاسم كما هي دون اختراع كود.


سؤال الطالب:
{user_message}
""".strip()

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        text = getattr(response, "text", None)
        if text and text.strip():
            return text.strip()

        return "عذرًا، لم أستطع توليد إجابة مناسبة الآن."
    except Exception:
        return "حدث خطأ أثناء محاولة الإجابة بالذكاء الاصطناعي. حاول مرة أخرى لاحقًا."


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_message = str(data.get("message", "")).strip()

    if not user_message:
        return jsonify({"reply": "الرجاء كتابة رسالة أولًا."})

    try:
        links = load_links()
    except Exception:
        links = {}

    intent = detect_intent(user_message)

    if intent == "general_ai":
        return jsonify({"reply": ask_gemini(user_message)})

    best_score, best_link = find_best_link(user_message, links)

    if intent == "course_like":
        if best_score >= 0.60:
            return jsonify({"reply": best_link})

        
        return jsonify({"reply": ask_gemini(user_message)})

    if best_score >= 0.72:
        return jsonify({"reply": best_link})

    return jsonify({"reply": ask_gemini(user_message)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
