import json
import os
import re
import sys
import time

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

from prompt import SYSTEM_PROMPT

# Add rag/ folder to path
sys.path.append(os.path.join(os.path.dirname(__file__), "rag"))
from query import get_relevant_chunks, get_chapters_for_question
from chapters import CHAPTERS

load_dotenv()

# ── DUAL MODEL SETUP ──
# Gemini Flash 2.5 — cheap, fast, good for theory/conversation
# Used for ~80% of messages
flash_llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.7,
)

# Claude Haiku 4.5 — better arithmetic precision, NCTB-format math
# Used for ~20% of messages (math/calculation problems)
haiku_llm = ChatOpenAI(
    model="anthropic/claude-haiku-4-5",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.5,  # Lower for math precision
)

# Claude Sonnet 4.5 — strongest tier, used for image-based math problems
# where vision + Bangla numeral OCR + arithmetic all need to work together
sonnet_llm = ChatOpenAI(
    model="anthropic/claude-sonnet-4-5",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.5,
)
gemini_pro_llm = ChatOpenAI(
    model="google/gemini-2.5-pro",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.5,
)
gpt54_mini_llm = ChatOpenAI(
    model="openai/gpt-5.4-mini",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.5,
)

parser = StrOutputParser()
flash_chain = flash_llm | parser
haiku_chain = haiku_llm | parser
sonnet_chain = sonnet_llm | parser
gemini_pro_chain = gemini_pro_llm | parser
gpt54_mini_chain = gpt54_mini_llm | parser


# ── MATH DETECTION ──
# These patterns suggest the question requires precise arithmetic
MATH_KEYWORDS_BANGLA = [
    "জাবেদা", "খতিয়ান", "রেওয়ামিল", "বিবরণী", "বিক্রয়মূল্য",
    "ক্রয়মূল্য", "অবচয়", "depreciation", "ব্যয়", "মুনাফা",
    "নির্ণয়", "বের কর", "হিসাব কর", "প্রস্তুত কর", "তৈরি কর",
    "প্রাপ্তি ও প্রদান", "আয়-ব্যয়", "আর্থিক অবস্থা",
    "balance sheet", "trial balance", "ledger", "journal",
    "মোট ব্যয়", "মোট আয়", "মোট মূলধন", "নগদ তহবিল",
    "মূলধন", "সম্পদ", "দায়", "মালিকানা স্বত্ব",
]

# Patterns that signal a multi-step problem needing Pro (accounting + general math only)
COMPLEX_MATH_KEYWORDS = [
    # Accounting — always multi-step
    "জাবেদা", "খতিয়ান", "রেওয়ামিল", "বিবরণী",
    "balance sheet", "trial balance", "ledger", "journal",
    "প্রাপ্তি ও প্রদান", "আয়-ব্যয়", "আর্থিক অবস্থা",
    # Explicit multi-step signals (accounting/algebra context only)
    "ধাপে ধাপে", "সমাধান কর", "প্রমাণ কর",
    "প্রস্তুত কর", "তৈরি কর",
    # Chemistry
    "মোলার", "মোল", "stoichiometry", "বিক্রিয়া সমীকরণ",
    # Multi-equation / algebra
    "সমীকরণ সমাধান", "simultaneous", "দুটি সমীকরণ",
]

# Physics keywords — routed to Flash, not Pro
PHYSICS_KEYWORDS = [
    "বেগ", "ত্বরণ", "বল", "কাজ", "ক্ষমতা", "শক্তি", "ভরবেগ",
    "তরঙ্গদৈর্ঘ্য", "কম্পাঙ্ক", "প্রতিরোধ", "তড়িৎ",
    "পদার্থ", "পদার্থবিজ্ঞান", "physics", "নিউটন", "মহাকর্ষ",
    "চাপ", "তাপ", "আলো", "প্রতিফলন", "প্রতিসরণ",
    "বিদ্যুৎ", "চৌম্বক", "তেজস্ক্রিয়",
]


def is_physics_question(user_input: str) -> bool:
    """Returns True if the question is about physics."""
    text = user_input.lower()
    return any(kw.lower() in text for kw in PHYSICS_KEYWORDS)

# Bangla numerals (০-৯) and English numerals (0-9) — a question with multiple
# numbers is almost always math
BANGLA_DIGITS = "০১२३४५६७८९"
ENGLISH_DIGITS = "0123456789"


def is_math_question(user_input: str) -> bool:
    """
    Detect if the question requires precise arithmetic.
    Returns True if we should route to Haiku, False for Flash.

    Heuristics (any one triggers math routing):
    1. Contains 3+ numbers (Bangla or English)
    2. Contains math operators (×, ÷, +, −, %)
    3. Contains accounting math keywords + 1+ numbers
    4. Contains explicit calculation requests
    """
    if not user_input:
        return False

    text = user_input.lower()

    # Find sequences of digits (each is a "number")
    bangla_numbers = re.findall(r"[০-৯]+", user_input)
    english_numbers = re.findall(r"\d+", user_input)
    number_count = len(bangla_numbers) + len(english_numbers)

    # 1. Strong signal: 3+ distinct numbers in the question
    if number_count >= 3:
        return True

    # 2. Strong signal: math operators present
    math_operators = ["×", "÷", "%", "=", "+−"]
    if any(op in user_input for op in math_operators):
        return True

    # 3. Math keyword + at least 1 number
    has_math_keyword = any(kw in text for kw in [k.lower() for k in MATH_KEYWORDS_BANGLA])
    if has_math_keyword and number_count >= 1:
        return True

    # 4. Explicit calculation request keywords
    calc_phrases = [
        "নির্ণয় কর", "বের কর", "হিসাব কর", "প্রস্তুত কর",
        "তৈরি কর", "calculate", "compute", "find the",
    ]
    if any(phrase in text for phrase in calc_phrases):
        return True

    return False


def is_complex_math(user_input: str) -> bool:
    """
    Returns True for multi-step problems that need Pro:
    - Accounting (journal, ledger, balance sheet, etc.)
    - Physics/chemistry with multiple formula steps
    - Problems with 5+ numbers (multi-step word problems)
    - Explicit step-by-step solve requests
    Simple math (1-2 steps, single formula) returns False → Flash.
    """
    if not user_input:
        return False
    text = user_input.lower()

    if any(kw.lower() in text for kw in COMPLEX_MATH_KEYWORDS):
        return True

    # 5+ distinct numbers → almost certainly multi-step
    bangla_numbers = re.findall(r"[০-৯]+", user_input)
    english_numbers = re.findall(r"\d+", user_input)
    if len(bangla_numbers) + len(english_numbers) >= 5:
        return True

    return False


def pick_chain(user_input: str, subject: str = ""):
    """Select which LLM based on detected subject + question type.

    Physics → always Flash (subject is authoritative over keyword heuristics)
    Accounting + complex math → Pro
    Everything else → Flash
    """
    # Subject is the primary gate — never send physics to Pro
    if subject == "physics" or is_physics_question(user_input):
        print(f"⚛️ [Routing] Physics → Gemini 2.5 Flash")
        return flash_chain
    # Only route to Pro for accounting or genuine multi-step math on non-physics subjects
    if subject == "accounting" or is_complex_math(user_input):
        print(f"🧮 [Routing] Accounting/complex math → Gemini 2.5 Pro")
        return gemini_pro_chain
    print(f"💬 [Routing] Theory/simple → Gemini 2.5 Flash")
    return flash_chain


# ── TOC SHORT-CIRCUIT ──
TOC_KEYWORDS = [
    "chapter", "অধ্যায়", "তালিকা", "syllabus", "chapter gula", "কয়টি", "koyta",
]

SUBJECT_ALIASES = {
    "biology": ["biology", "bio", "জীববিজ্ঞান", "জীব বিজ্ঞান", "জিববিজ্ঞান"],
    "geography": ["geography", "geo", "bugol", "bhugol", "ভূগোল", "ভুগোল", "bugol o poribesh"],
    "accounting": ["accounting", "হিসাববিজ্ঞান", "হিসাব", "hisoab", "account"],
    "physics": ["physics", "পদার্থবিজ্ঞান", "পদার্থ", "পদার্থ বিজ্ঞান", "podartho", "podarthobiggyan"],
}


# Distinctive content keywords per subject — used to auto-detect subject from question text
_SUBJECT_CONTENT_KEYWORDS = {
    "biology": [
        "সালোকসংশ্লেষণ", "photosynthesis", "কোষ", "cell", "উদ্ভিদ", "প্রাণী",
        "শ্বসন", "respiration", "রক্ত", "blood", "হৃদয়", "heart", "ফুসফুস",
        "বাস্তুতন্ত্র", "ecosystem", "ব্যাকটেরিয়া", "bacteria", "ভাইরাস", "virus",
        "ক্লোরোফিল", "chlorophyll", "মাইটোকন্ড্রিয়া", "mitochondria",
        "ক্রোমোজোম", "chromosome", "জিন", "gene", "dna", "rna",
        "মাইটোসিস", "mitosis", "মিয়োসিস", "meiosis",
        "জীব", "টিস্যু", "tissue", "অঙ্গ", "organ",
    ],
    "physics": [
        "বেগ", "velocity", "ত্বরণ", "acceleration", "বল", "force",
        "ঘর্ষণ", "friction", "নিউটন", "newton", "ভরবেগ", "momentum",
        "বিদ্যুৎ", "electricity", "চুম্বক", "magnet", "তরঙ্গ", "wave",
        "শব্দ", "sound", "আলো", "light", "তাপ", "heat", "চাপ", "pressure",
        "ক্ষমতা", "power", "কাজ", "work", "শক্তি", "energy",
        "প্রতিসরণ", "refraction", "প্রতিফলন", "reflection",
    ],
    "chemistry": [
        "পরমাণু", "atom", "অণু", "molecule", "রাসায়নিক", "chemical",
        "বিক্রিয়া", "reaction", "যৌগ", "compound", "মৌল", "element",
        "অ্যাসিড", "acid", "ক্ষার", "base", "লবণ", "salt",
        "ইলেকট্রন", "electron", "প্রোটন", "proton", "নিউট্রন", "neutron",
        "পর্যায় সারণি", "periodic table",
    ],
    "geography": [
        "ভূগোল", "মানচিত্র", "map", "জলবায়ু", "climate", "নদী", "river",
        "পর্বত", "mountain", "মহাদেশ", "continent", "সাগর", "ocean",
        "বৃষ্টিপাত", "rainfall", "ভূমিকম্প", "earthquake",
        "জনসংখ্যা", "population", "মৃত্তিকা", "soil",
    ],
    "accounting": [
        "হিসাব", "লেজার", "ledger", "জাবেদা", "journal", "ক্রেডিট", "credit",
        "ডেবিট", "debit", "ব্যালেন্স শিট", "balance sheet", "মুনাফা", "profit",
        "ক্ষতি", "loss", "আর্থিক", "financial", "trial balance", "নগদ",
    ],
}


def detect_subject_from_question(text: str, fallback: str = "biology") -> str:
    """Detect subject from question content keywords. Returns fallback if unclear."""
    t = text.lower()
    scores = {subj: 0 for subj in _SUBJECT_CONTENT_KEYWORDS}
    for subj, keywords in _SUBJECT_CONTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in t:
                scores[subj] += 1
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    return fallback


CASUAL_PATTERNS = [
    "হ্যালো", "হ্যালো!", "hello", "hi ", "hi!", "হাই", "আসসালামু",
    "সালাম", "কেমন আছ", "কেমন আছো", "কেমন আছেন", "ভালো আছ",
    "ভালো আছো", "কী খবর", "ki khobor", "what's up", "whats up",
    "ধন্যবাদ", "thanks", "thank you", "শুক্রিয়া",
    "আবার আসব", "bye", "বিদায়", "ok", "okay", "ঠিক আছে",
    "বুঝলাম", "বুঝেছি", "got it",
    "kon kon subject", "কোন কোন subject", "কোন subject", "কী subject",
    "ki subject", "which subject", "what subject", "কী পড়াও", "কী পড়ান",
    "kon subject poran", "apni ki poran", "tumi ki poran",
]

def is_casual_chat(user_input: str) -> bool:
    """Returns True for greetings/thanks/casual — skip RAG and stage indicators."""
    text = user_input.strip().lower()
    if len(text) <= 10:
        return True
    return any(p in text for p in CASUAL_PATTERNS)


# ── INSTANT GREETING RESPONSES (zero LLM cost) ──
import random

_GREETING_REPLIES = [
    "হ্যালো! আজকে কী পড়তে ইচ্ছা করছে তোমার? 🌱",
    "হ্যালো! বলো তো, আজকে কোন বিষয় নিয়ে সাহায্য লাগবে? 😊",
    "আরে হ্যালো! কী জানতে চাও বলো, আপু বুঝিয়ে দিচ্ছি 🌸",
    "হ্যালো! আজকে কোন চ্যাপ্টারটা একদম পানির মতো সহজ করতে হবে বলো? ✨",
    "আরে ওয়াও! আজকে তো বেশ জলদি জলদি পড়তে চলে আসলে। বলো, কোনটা দিয়ে শুরু করব? 🚀",
    "হ্যালো! পড়ার টেবিলে মন বসছে না বুঝি? চল, একসাথে বসে কোনো একটা কঠিন টপিক সহজ করে ফেলি! 📘🌱",
    "হ্যালো! পরীক্ষার প্রস্তুতি কেমন চলছে তোমার? আজকে কোন জিনিসটা রিভিশন করতে চাও বলো? 🎯",
    "আরে হ্যালো! আজকে কিন্তু দারুণ একটা টপিক একদম গল্পে গল্পে বুঝে ফেলব। রেডি তো? ✨",
    "হ্যালো! আজকেও কি বিজ্ঞানের কোনো জটিল রহস্য পানির মতো সহজ করতে হবে? বলো, আপু রেডি! 🧠🌸"
]

_THANKS_REPLIES = [
    "আরে, ধন্যবাদের কী আছে! আরও কিছু বুঝতে চাইলে নির্দ্বিধায় বলো 🌱",
    "আরে না না, থ্যাংকস বলা লাগবে না! আর কোনো প্রশ্ন থাকলে চট করে জিজ্ঞেস করে ফেলো 😊",
    "আরে ধুর! এটাই তো আমার কাজ। পড়াটা বুঝতে পেরেছ তো, নাকি আরও কিছু লাগবে? 🌸",
    "হাহা, থ্যাংক ইউ বলা লাগবে না একদম! পড়াটা মাথায় ঢুকলেই আপু খুশি। আর কোনো খটকা আছে? 🚀",
    "আরে চমৎকার! তোমার উপকারে আসলেই আমার ভালো লাগে। পরের কোন টপিকটা দেখবে বলো? ✨"
]

_BYE_REPLIES = [
    "ঠিক আছে, তাহলে আজকে এই পর্যন্তই! পড়ালেখা কিন্তু ফাঁকি দেওয়া যাবে না, ভালোমতো কোরো 🌱",
    "বাই বাই! যখনই কোনো পড়া আটকে যাবে, জাস্ট আপুকে নক দিও 🌸",
    "আবার দেখা হবে! মন দিয়ে পড়াশোনা কোরো আর ভালো থেকো কিন্তু ✨",
    "আচ্ছা যাও, আজকে অনেক পড়াশোনা হয়েছে! একটু রেস্ট নিয়ে আবার টেবিলে বসো কিন্তু। টা-টা! 🚀",
    "ওকে রে, ভালোমতো প্রিপারেশন নাও। কোনো সমস্যা হলে আপু তো আছিই, বাই! 📘"
]

_OK_REPLIES = [
    "আচ্ছা ঠিক আছে! আর কিছু লাগলে বলো কিন্তু 🌱",
    "ওকে! কোনো খটকা থাকলে বা প্রশ্ন থাকলে নির্দ্বিধায় জিজ্ঞেস করো 😊",
    "একদম ঠিক! চল তাহলে পরের অংশটা দেখে ফেলি? ✨",
    "আচ্ছা বুঝলাম! এই ব্যাপারে আর কোনো প্রশ্ন আছে তোমার? 🌸"
]

_SUBJECT_REPLIES = [
    "আমি তোমাকে **জীববিজ্ঞান**, **পদার্থবিজ্ঞান**, **রসায়ন**, **ভূগোল**, আর **হিসাববিজ্ঞান** — একদম SSC NCTB সিলেবাস অনুযায়ী বুঝিয়ে দেবো। বলো, কোনটা দিয়ে শুরু করবে আজকে? 🌱",
    "আপু তোমাকে **জীববিজ্ঞান**, **পদার্থবিজ্ঞান**, **রসায়ন**, **ভূগোল** এবং **হিসাববিজ্ঞান** পড়তে সাহায্য করতে পারব। এর মধ্যে কোনটা আজ পানির মতো সহজ করতে চাও বলো? 🎯"
]

_THANKS_TRIGGERS   = ["ধন্যবাদ", "thanks", "thank you", "শুক্রিয়া", "থ্যাংকস", "থ্যাঙ্কু", "dhonnobad", "thx", "thanku", "tq"]
_SUBJECT_TRIGGERS  = ["kon kon subject", "which subject", "what subject", "কী পড়াও",
                      "কী পড়ান", "kon subject", "apni ki poran", "tumi ki poran",
                      "কোন subject", "ki subject", "কোন কোন বিষয়", "কি কি বিষয়", "কোন বিষয়", "কি কি সাবজেক্ট", "কোন সাবজেক্ট"]

def instant_reply(user_input: str) -> str | None:
    """
    Return a hardcoded reply instantly for pure greetings/thanks/bye/ok.
    Returns None if the message needs LLM processing.
    """
    text = user_input.strip().lower()
    words = set(text.split())  # word-level set for short English triggers

    if any(t in text for t in _SUBJECT_TRIGGERS):
        return random.choice(_SUBJECT_REPLIES)

    # Greetings — "hi"/"hello" must be standalone words, not inside longer words
    if words & {"hi", "hello", "assalamu"} or any(t in text for t in ["হ্যালো", "হাই", "আসসালামু", "সালাম"]):
        return random.choice(_GREETING_REPLIES)

    if any(t in text for t in _THANKS_TRIGGERS):
        return random.choice(_THANKS_REPLIES)

    # Bye — "bye" must be standalone
    if words & {"bye"} or any(t in text for t in ["বিদায়", "আবার আসব", "আবার আসবো"]):
        return random.choice(_BYE_REPLIES)

    # OK — "ok"/"okay" must be standalone words, not substrings (e.g. "salok", "okay-ish")
    if words & {"ok", "okay", "hm", "hmm"} or any(t in text for t in ["ঠিক আছে", "বুঝলাম", "বুঝেছি", "got it", "আচ্ছা"]):
        return random.choice(_OK_REPLIES)

    return None


def is_toc_question(user_input: str) -> bool:
    """Detect if user is asking 'list all chapters' type questions."""
    text = user_input.lower()
    has_chapter_word = any(kw in text for kw in TOC_KEYWORDS)
    # Must mention chapter/অধ্যায় AND ask a list-type question
    asks_list = any(w in text for w in [
        "ki ki", "kiki", "kon kon", "কোন কোন", "কী কী",
        "name", "নাম", "list", "তালিকা", "gula", "গুলো",
    ])
    return has_chapter_word and asks_list


def detect_subject_in_question(user_input: str, fallback: str = "biology") -> str:
    """
    If user explicitly mentions a subject in their question,
    return that subject. Otherwise return fallback.
    """
    text = user_input.lower()
    for subject, aliases in SUBJECT_ALIASES.items():
        for alias in aliases:
            if alias.lower() in text:
                return subject
    return fallback


def build_toc_response(subject: str) -> str:
    """Generate a guaranteed-correct chapter list from chapters.py registry."""
    if subject not in CHAPTERS:
        return ""

    chapters = CHAPTERS[subject]
    total = len(chapters)

    subject_label = {
        "biology": "জীববিজ্ঞান",
        "geography": "ভূগোল ও পরিবেশ",
        "accounting": "হিসাববিজ্ঞান",
    }.get(subject, subject.capitalize())

    intro = f"চলো, {subject_label} বইয়ের সব অধ্যায়ের নাম দেখে নিই 🌱\n\nএই বইয়ে মোট **{total}টি অধ্যায়** আছে:\n\n"

    chapter_lines = []
    for _, (num, title) in chapters.items():
        chapter_lines.append(f"{num}. {title}")

    body = "\n".join(chapter_lines)

    outro = "\n\nকোন অধ্যায়টা নিয়ে জানতে চাও বলো তো? বললেই আপু বুঝিয়ে দিচ্ছি! 🌸"

    return intro + body + outro


def parse_quiz_request(text: str) -> int:
    """Returns MCQ count if message is a quiz request, else 0."""
    t = text.lower()
    if 'quiz' not in t and 'কুইজ' not in t:
        return 0
    m = re.search(r'(\d+)\s*(?:ta\b|to\b|টা|ti\b|টি)', t)
    if m:
        return min(int(m.group(1)), 20)
    return 1


# ── GUIDE MODE: step-by-step chapter learning ──

_ROADMAP_KEYWORDS = [
    'roadmap', 'রোডম্যাপ', 'guide mode',
    'ধাপে ধাপে পড়', 'শুরু থেকে পড়', 'পুরোটা পড়', 'পুরো পড়তে চাই',
    'শেষ করতে চাই', 'শুরু করতে চাই', 'পড়া শুরু করতে চাই',
    'কোথা থেকে শুরু', 'কীভাবে পড়ব', 'কীভাবে শুরু করব',
    'step by step পড়', 'পথনির্দেশ',
]


_DESPAIR_KEYWORDS = [
    'kisui bujhina', 'kisui bujtesina', 'kichhu bujhi na', 'bujhi na', 'bujhte parchi na',
    'কিছুই বুঝি না', 'কিছু বুঝি না', 'বুঝতে পারছি না', 'বুঝছি না',
    'অনেক কঠিন', 'খুব কঠিন', 'very hard', 'too hard', 'difficult',
    'ভয় লাগছে', 'ভয় লাগে', 'কঠিন লাগছে', 'পারছি না', 'parchina',
    'give up', 'দুর্বল', 'weak in', 'not good at', 'ami weak',
    'শুরু করতে পারছি না', 'কোথা থেকে শুরু করব',
]


def is_despair(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in _DESPAIR_KEYWORDS)


_ROADMAP_SUBJECT_ALIASES = [
    (['physics', 'phys', 'পদার্থ', 'physic'], 'physics'),
    (['biology', 'bio', 'জীববিজ্ঞান', 'জীব বিজ্ঞান', 'biolog'], 'biology'),
    (['accounting', 'account', 'হিসাব', 'acounting', 'accounts'], 'accounting'),
    (['geography', 'geo', 'ভূগোল', 'geograph'], 'geography'),
]


def detect_subject_for_roadmap(text: str):
    """Typo-tolerant subject detection for roadmap requests. Returns subject string or None."""
    t = text.lower()
    for keywords, subject in _ROADMAP_SUBJECT_ALIASES:
        if any(k in t for k in keywords):
            return subject
    return None


def is_roadmap_request(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in _ROADMAP_KEYWORDS)


_EN_ORDINALS = {
    'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
    'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
    'eleventh': 11, 'twelfth': 12, 'thirteenth': 13, 'fourteenth': 14, 'fifteenth': 15,
}


def detect_chapter_from_message(text: str, subject: str):
    """Extract (chapter_num, chapter_title) from message. Returns None if not found."""
    chapters = CHAPTERS.get(subject, {})
    t = text.lower()

    # Match Bengali ordinal words or chapter title substring
    for ordinal, (num, title) in chapters.items():
        if ordinal in t or title.lower() in t:
            return num, title

    # Match English ordinals ("second chapter", "third chapter")
    for word, n in _EN_ORDINALS.items():
        if word in t:
            for _, (num, title) in chapters.items():
                if num == n:
                    return num, title

    # Match "chapter N" / "অধ্যায় N" with ASCII or Bengali digits
    m = re.search(r'(?:chapter|অধ্যায়)\s*[:\s]*([০-৯0-9]+)', t)
    if m:
        n = int(m.group(1).translate(str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')))
        for _, (num, title) in chapters.items():
            if num == n:
                return num, title

    return None


def generate_section_list(subject: str, chapter_num: int, chapter_title: str) -> list:
    """Return NCTB sub-sections for a chapter. Uses hardcoded data; Flash is fallback only."""
    from rag.chapters import CHAPTER_SECTIONS
    hardcoded = CHAPTER_SECTIONS.get(subject, {}).get(chapter_num)
    if hardcoded:
        print(f"[roadmap] Using hardcoded sections for {subject} ch{chapter_num}")
        return hardcoded

    # Flash fallback for subjects not yet hardcoded (e.g. chemistry)
    print(f"[roadmap] No hardcoded sections for {subject} ch{chapter_num} — asking Flash")
    _f = flash_llm | StrOutputParser()
    prompt = (
        f"SSC NCTB {subject} বই এর অধ্যায় {chapter_num}: \"{chapter_title}\"\n"
        f"এই অধ্যায়ের sub-section গুলো NCTB বই এর হুবহু ক্রমে দাও।\n"
        f"শুধু JSON array — অন্য কোনো text না:\n"
        f'["{chapter_num}.১ নাম", "{chapter_num}.২ নাম", ...]'
    )
    try:
        raw = _f.invoke([HumanMessage(content=prompt)]).strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        sections = json.loads(raw)
        return [s for s in sections if isinstance(s, str) and s.strip()]
    except Exception as e:
        print(f"[roadmap] section list error: {e}")
        return []


def generate_quiz_mcq(history: list, subject: str = "biology", user_query: str = "") -> dict:
    """
    Generate one MCQ. Two modes:
    - Studying mode (has real conversation): quiz ONLY from what was taught, no RAG
    - Cold start (no/minimal convo): quiz from RAG using user's topic/chapter query
    Returns {} on error, {"exhausted": True} when no more distinct questions possible,
    {"no_topic": True} when cold start but no topic specified.
    """
    # Extract previously asked questions + answers so the LLM avoids semantic repeats
    asked = []
    for m in history:
        content = str(m.get('content', ''))
        if content.startswith('🎯 Quiz\n'):
            lines = content.split('\n')
            q = lines[1] if len(lines) > 1 else ''
            ans_line = next((l for l in lines if l.startswith('[সঠিক উত্তর:')), '')
            if q:
                asked.append(f"{q} ({ans_line})" if ans_line else q)

    # Real study messages = non-quiz, non-chip messages
    study_messages = [
        m for m in history
        if isinstance(m.get('content'), str)
        and not m.get('content', '').startswith('__')
        and not m.get('content', '').startswith('🎯 Quiz')
        and m.get('content', '').strip() not in ('Quiz করো 🎯', 'বুঝেছি', 'পরবর্তী প্রশ্ন')
    ]
    has_study_session = len(study_messages) >= 4  # meaningful conversation

    convo = "\n".join(
        f"{'ছাত্র' if m['role']=='user' else 'দীপ্তি'}: {str(m.get('content',''))[:300]}"
        for m in study_messages[-12:]
    )

    book_context = ""

    if has_study_session:
        # Studying mode: quiz only from conversation, no RAG
        # If questions already asked outnumber study messages, topic is exhausted
        if len(asked) >= max(3, len(study_messages) // 2):
            return {"exhausted": True}
        content_block = f"ছাত্র এই session-এ যা পড়েছে:\n{convo}"
        source_rule = "শুধুমাত্র উপরের কথোপকথনে যা আলোচনা হয়েছে সেখান থেকে প্রশ্ন তৈরি করো — textbook থেকে নতুন কিছু আনবে না"
    else:
        # Cold start: no real study session, use RAG from user's topic query
        if not user_query.strip():
            return {"no_topic": True}
        try:
            from rag.query import get_relevant_chunks
            book_context = get_relevant_chunks(user_query.strip(), subject=subject, top_k=5)
        except Exception as e:
            print(f"[quiz] RAG error: {e}")
        if not book_context:
            return {"no_topic": True}
        content_block = f"পাঠ্যপুস্তকের অংশ:\n{book_context}"
        source_rule = "উপরের পাঠ্যপুস্তকের অংশ থেকে একটি factual MCQ প্রশ্ন তৈরি করো"

    asked_block = ""
    if asked:
        asked_block = "\n\nইতিমধ্যে এই প্রশ্ন ও concept গুলো cover হয়েছে — একই concept ভিন্নভাবে জিজ্ঞেস করো না:\n" + "\n".join(f"- {q}" for q in asked)

    prompt = f"""তুমি SSC পরীক্ষার প্রশ্ন তৈরি করছ।

{content_block}{asked_block}

নিয়ম:
- {source_rule}
- প্রতিটি প্রশ্ন আলাদা concept cover করবে — একই ধারণা ভিন্নভাবে জিজ্ঞেস করবে না
- যদি নতুন আলাদা প্রশ্ন তৈরি করা সম্ভব না হয়, শুধু {{"exhausted": true}} দাও
- প্রশ্ন ও explanation-এ "কথোপকথন অনুসারে" বা "Dipti বলেছে" জাতীয় কিছু লিখবে না
- প্রশ্নটি সরাসরি factual হবে
- explanation একটি সংক্ষিপ্ত factual কারণ হবে
- চারটি অপশন স্পষ্ট ও আলাদা হবে

শুধু JSON দাও:
{{
  "question": "প্রশ্ন বাংলায়",
  "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
  "correct": "B",
  "explanation": "এক বাক্যে কারণ"
}}"""

    try:
        raw = (flash_llm | StrOutputParser()).invoke([HumanMessage(content=prompt)]).strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        if data.get("exhausted"):
            return {"exhausted": True}
        if not all(k in data for k in ("question", "options", "correct", "explanation")):
            return {}
        if not all(k in data["options"] for k in ("A", "B", "C", "D")):
            return {}
        return data
    except Exception as e:
        print(f"[quiz] generate error: {e}")
        return {}


# ── STREAM DEFINITIONS ──
STREAM_INFO = {
    "science": {
        "name": "বিজ্ঞান বিভাগ",
        "subjects": ["biology", "physics", "chemistry"],
        "label": "Biology, Physics, Chemistry, Higher Math",
    },
    "commerce": {
        "name": "ব্যবসায় শিক্ষা বিভাগ",
        "subjects": ["accounting", "economics"],
        "label": "Accounting, Economics, Finance & Banking, Business Studies",
    },
    "arts": {
        "name": "মানবিক বিভাগ",
        "subjects": ["geography", "history", "civics"],
        "label": "Geography, History, Civics, Economics",
    },
}

def get_stream_for_subject(subject: str) -> str | None:
    for stream, info in STREAM_INFO.items():
        if subject in info["subjects"]:
            return stream
    return None

def check_stream_mismatch(user_stream: str, question_subject: str) -> str | None:
    """
    Returns a soft-redirect message if the question subject is outside
    the student's stream. Returns None if it's fine to answer.
    """
    if not user_stream or user_stream not in STREAM_INFO:
        return None
    allowed = STREAM_INFO[user_stream]["subjects"]
    if question_subject in allowed:
        return None
    # Subject belongs to a different stream
    other_stream = get_stream_for_subject(question_subject)
    if not other_stream:
        return None  # common subject — always allowed
    stream_name  = STREAM_INFO[user_stream]["name"]
    other_name   = STREAM_INFO[other_stream]["name"]
    subject_labels = {
        "biology": "জীববিজ্ঞান", "physics": "পদার্থবিজ্ঞান",
        "chemistry": "রসায়ন", "accounting": "হিসাববিজ্ঞান",
        "geography": "ভূগোল", "economics": "অর্থনীতি",
    }
    subj_label = subject_labels.get(question_subject, question_subject)
    return (
        f"তুমি **{stream_name}**-এ আছো, তাই **{subj_label}** তোমার সিলেবাসে নেই 🌱\n\n"
        f"এই বিষয়টি **{other_name}**-এর শিক্ষার্থীদের জন্য।\n\n"
        f"**{subj_label}** পড়তে চাইলে নতুন চ্যাটে **{other_name}** নির্বাচন করো।\n"
        f"আর যদি শুধু জানার আগ্রহ থেকে প্রশ্ন করে থাকো, তাহলে সেটাও বলতে পারো 😊"
    )


def _transliterate_name_to_bangla(name: str) -> str:
    """Approximate Latin-to-Bangla transliteration for personal names."""
    cons = {
        'b': 'ব', 'c': 'ক', 'd': 'দ', 'f': 'ফ', 'g': 'গ',
        'h': 'হ', 'j': 'জ', 'k': 'ক', 'l': 'ল', 'm': 'ম',
        'n': 'ন', 'p': 'প', 'q': 'ক', 'r': 'র', 's': 'স',
        't': 'ত', 'v': 'ভ', 'w': 'ওয়', 'x': 'ক্স', 'y': 'য়', 'z': 'জ',
    }
    ind_v = {'a': 'আ', 'e': 'এ', 'i': 'ই', 'o': 'ও', 'u': 'উ'}
    dep_v = {'a': 'া', 'e': 'ে', 'i': 'ি', 'o': 'ো', 'u': 'ু'}

    s = name.lower().strip()
    if not s:
        return name

    # If name already contains Bangla characters, return as-is
    if any('ঀ' <= ch <= '৿' for ch in name):
        return name

    result = []
    prev_type = 'start'  # 'start', 'consonant', 'vowel'

    for ch in s:
        if ch in ind_v:
            if prev_type == 'consonant':
                result.append(dep_v[ch])
            elif prev_type == 'vowel' and ch in ('a', 'e', 'o', 'u'):
                # vowel-after-vowel: insert য় connector (e.g. "ia" → "িয়া", "ea" → "েয়া")
                result.append('য়' + dep_v[ch])
            else:
                result.append(ind_v[ch])
            prev_type = 'vowel'
        elif ch in cons:
            result.append(cons[ch])
            prev_type = 'consonant'
        # skip other chars (spaces, hyphens etc.)

    return ''.join(result) or name


def build_system_prompt(nctb_context: str, project_instructions: str = "", stream: str = "", student_name: str = "", student_profile: dict = None) -> str:
    """Build the full system prompt with NCTB context + optional project instructions."""
    prompt = SYSTEM_PROMPT

    if student_name and student_name.strip():
        first_name = student_name.strip().split()[0]
        bangla_name = _transliterate_name_to_bangla(first_name)
        prompt += f"""

## ছাত্রের নাম
ছাত্রের নাম: {bangla_name}
মাঝে মাঝে (সব সময় না) নাম ধরে ডাকো — যখন স্বাভাবিক লাগে।
⚠️ HARD RULE: নাম লেখার সময় শুধু "{bangla_name}" লিখবে। কখনো "{first_name}" বা অন্য কোনো Latin/English script-এ লিখবে না।
"""

    if student_profile:
        weak    = student_profile.get('weak_topics') or []
        strong  = student_profile.get('strong_topics') or []
        confuse = student_profile.get('confusion_signals') or []
        last    = student_profile.get('last_session_topic') or ''

        profile_lines = []
        if last:
            profile_lines.append(f"আগের session-এ শেষ পড়েছিল: {last}")
        if weak:
            profile_lines.append(f"দুর্বল topic (এই ছাত্র এখানে বারবার আটকেছে): {', '.join(weak[-5:])}")
        if strong:
            profile_lines.append(f"শক্তিশালী topic (ভালো বোঝে): {', '.join(strong[-3:])}")
        if confuse:
            profile_lines.append(f"আগে যেখানে confusion হয়েছিল: {', '.join(confuse[-3:])}")

        if profile_lines:
            prompt += f"""

## এই ছাত্রের লার্নিং প্রোফাইল:
{chr(10).join('- ' + p for p in profile_lines)}

## দুর্বল topic নিয়ে তোমার concrete দায়িত্ব:
১. দুর্বল topic কথায় উঠলে — ধীরে বোঝাও, analogy দাও, তাড়াহুড়া করবে না
২. দুর্বল topic explain করার পরে জিজ্ঞেস করো: "এবার কি পরিষ্কার হলো?"
৩. নতুন topic পড়ানোর সময় দুর্বল topic-এর সাথে connection দেখাও যদি সম্পর্ক থাকে
৪. ছাত্র একই concept-এ ২+ বার ভুল করলে বলো: "এটা তোমার weak point মনে হচ্ছে — চলো অন্যভাবে বুঝাই"
৫. শক্তিশালী topic এলে তুলনামূলক দ্রুত এগোতে পারো — ছাত্র এটা জানে

⚠️ দুর্বল topic মানে ছাত্র বোকা না — সে চেষ্টা করছে। ধৈর্য ধরে, positive রেখে পড়াও।
"""

    if stream and stream in STREAM_INFO:
        info = STREAM_INFO[stream]
        prompt += f"""

## ছাত্রের বিভাগ (Stream):

এই ছাত্র SSC **{info['name']}**-এ পড়ে।
তার syllabus-এ আছে: {info['label']}

সবসময় এই বিভাগের context মাথায় রেখে উত্তর দাও।
"""

    if project_instructions and project_instructions.strip():
        prompt += f"""

## এই প্রজেক্টের বিশেষ নির্দেশনা (Project Instructions):

{project_instructions.strip()}

উপরের নির্দেশনা সবসময় মেনে চলবে।

---
"""

    if nctb_context and nctb_context.strip():
        # Detect if this is a TOC chunk — needs forceful verbatim handling
        is_toc_context = "Table of Contents" in nctb_context or "অধ্যায় তালিকা" in nctb_context

        if is_toc_context:
            prompt += f"""

## ⚠️ AUTHORITATIVE TEXTBOOK DATA — COPY EXACTLY:

নিচের তথ্য NCTB বইয়ের official Table of Contents। এটাই সঠিক, এর বাইরে তোমার কোনো knowledge ব্যবহার করবে না।

{nctb_context}

---

🚨 STRICT RULES for this answer:
1. উপরে যে chapter names আছে, সেগুলো EXACT-ভাবে copy করো — একটাও word change করবে না
2. মোট অধ্যায় সংখ্যা উপরে যা লেখা, সেটাই বলবে
3. কোনো chapter বাদ দিবে না, কোনোটা add করবে না
4. তোমার নিজের memory থেকে chapter names guess করবে না
5. শুধু উপরের list-টাই copy করো, কোনো paraphrase না

উপরে যত সংখ্যক chapter আছে, তুমি সেই সংখ্যকই বলবে — না বেশি, না কম।
"""
        else:
            prompt += f"""

## NCTB বই থেকে প্রাসঙ্গিক তথ্য:

{nctb_context}

---
উপরের তথ্য ব্যবহার করে উত্তর দাও.
"""

    return prompt


def do_rag_lookup(user_input: str, subject: str = "biology"):
    """
    Step 1: Run RAG retrieval ONLY. Returns (nctb_context, chapters_found).
    Separated so we can show progress to the user before the LLM call starts.
    """
    nctb_context = get_relevant_chunks(user_input, subject=subject)
    chapters_found = get_chapters_for_question(user_input, subject=subject)
    return nctb_context, chapters_found


def run_llm(user_input, history, nctb_context, project_instructions="", stream="", student_name="", subject="", student_profile: dict = None):
    """
    Run the LLM with already-retrieved context. Auto-picks Flash or Pro.
    Returns the final reply string.
    """
    system_with_context = build_system_prompt(nctb_context, project_instructions, stream=stream, student_name=student_name, student_profile=student_profile)
    # cache_control marks the system prompt as a cacheable prefix — OpenRouter/provider
    # will reuse the KV cache for this prefix across calls, cutting input cost ~90%
    messages = [SystemMessage(content=[{
        "type": "text",
        "text": system_with_context,
        "cache_control": {"type": "ephemeral"},
    }])]
    for msg in history:
        if msg["role"] == "user":
            img_url = msg.get("image_url")
            if img_url:
                messages.append(HumanMessage(content=[
                    {"type": "image_url", "image_url": {"url": img_url}},
                    {"type": "text", "text": msg["content"] or "এই ছবিটি দেখে বুঝিয়ে দাও।"},
                ]))
            else:
                messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_input))

    selected_chain = pick_chain(user_input, subject=subject)
    return selected_chain.invoke(messages)


def get_answer(user_input, history, project_instructions: str = "", subject: str = "biology"):
    """
    Non-streaming version (kept for backwards compat with /ask-image and tests).
    """
    # Auto-detect subject from question if user mentioned one explicitly
    detected_subject = detect_subject_in_question(user_input, fallback=subject)
    
    # ── EARLY EXIT: TOC questions get hardcoded perfect answers ──
    if is_toc_question(user_input):
        toc_reply = build_toc_response(detected_subject)
        if toc_reply:
            print(f"🎯 [TOC] Direct response for subject={detected_subject}")
            return {
                "reply": toc_reply,
                "chapters_found": ["অধ্যায় তালিকা (Table of Contents)"]
            }

    # Normal flow continues below
    t0 = time.time()
    nctb_context, chapters_found = do_rag_lookup(user_input, subject=detected_subject)

    print(f"\n📚 [DEBUG] User asked: {user_input}")
    print(f"📚 [DEBUG] Detected subject: {detected_subject}")
    print(f"📚 [DEBUG] Chapters found: {chapters_found}")
    print(f"📚 [DEBUG] Context length: {len(nctb_context)} chars")

    t1 = time.time()
    reply = run_llm(user_input, history, nctb_context, project_instructions, subject=detected_subject)
    t2 = time.time()

    print(f"⏱️  RAG: {t1-t0:.2f}s | LLM: {t2-t1:.2f}s | TOTAL: {t2-t0:.2f}s\n")

    return {
        "reply": reply,
        "chapters_found": chapters_found
    }


# Quick test mode
if __name__ == "__main__":
    history = []
    print("Test mode — type 'quit' to exit\n")
    while True:
        user_input = input("You: ")
        if user_input.lower() == "quit":
            break
        result = get_answer(user_input, history)
        answer = result["reply"] if isinstance(result, dict) else result
        print(f"Dipti: {answer}\n")
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": answer})