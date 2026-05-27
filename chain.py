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

# ── MODEL SETUP ──
# Gemini 2.5 Flash — main model: solving, vision, internal processing
flash_llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.7,
)
vision_llm = flash_llm  # same model — supports vision natively

# Gemini 2.5 Flash Lite — student-facing output: natural Bangla conversational responses
output_llm = ChatOpenAI(
    model="google/gemini-2.5-flash-lite",
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

# Claude Sonnet 4.5 — vision tier, used for image-based math problems
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
deepseek_llm = ChatOpenAI(
    model="deepseek/deepseek-v4-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.6,
)
deepseek_pro_llm = ChatOpenAI(
    model="deepseek/deepseek-v4-pro",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.5,
)

parser = StrOutputParser()
_img_extract_cache: dict[str, str] = {}  # url → extracted text, avoids re-running Gemini on same image
flash_chain = flash_llm | parser
output_chain = output_llm | parser
vision_chain = vision_llm | parser
haiku_chain = haiku_llm | parser
sonnet_chain = sonnet_llm | parser
gemini_pro_chain = gemini_pro_llm | parser
gpt54_mini_chain = gpt54_mini_llm | parser
deepseek_chain = deepseek_llm | parser
deepseek_pro_chain = deepseek_pro_llm | parser

# Student-selectable models (deepseek-pro retired — redirects to deepseek)
_STUDENT_CHAINS = {
    "gemini": flash_chain,
    "deepseek": deepseek_chain,
    "deepseek-pro": deepseek_chain,
}


def rewrite_to_bangla(draft: str) -> str:
    """Pass Gemini Flash draft through Gemini Flash Lite for natural Bangla output."""
    try:
        return output_chain.invoke([HumanMessage(content=(
            "তুমি দীপ্তি আপু। নিচের উত্তরটি হুবহু same content রেখে "
            "natural, প্রাকৃতিক বাংলায় rewrite করো। "
            "কোনো তথ্য, সংখ্যা বা ব্যাখ্যা বাদ দেবে না — শুধু ভাষা সুন্দর ও স্বাভাবিক করো।"
            f"\n\n{draft}"
        ))])
    except Exception as e:
        print(f"[rewrite] error: {e}")
        return draft


def _make_two_step_chain():
    from langchain_core.runnables import RunnableLambda

    def _to_rewrite_msgs(draft: str):
        return [HumanMessage(content=(
            "তুমি দীপ্তি আপু। নিচের উত্তরটি হুবহু same content রেখে "
            "natural, প্রাকৃতিক বাংলায় rewrite করো। "
            "কোনো তথ্য, সংখ্যা বা ব্যাখ্যা বাদ দেবে না — শুধু ভাষা সুন্দর ও স্বাভাবিক করো।"
            f"\n\n{draft}"
        ))]

    return flash_chain | RunnableLambda(_to_rewrite_msgs) | output_llm | parser


two_step_output_chain = _make_two_step_chain()


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


def classify_question(user_input: str) -> str:
    """
    Returns 'math', 'theory', or 'mixed'.
    'mixed' = has both a conceptual/explanation component AND an explicit calculation.
    Used for hybrid routing: Gemini handles theory, DeepSeek handles math.
    """
    text = user_input.lower()

    theory_signals = [
        "কী ", "কী?", "কাকে বলে", "কেন ", "কেন?", "ব্যাখ্যা", "সংজ্ঞা",
        "বর্ণনা", "পার্থক্য", "বৈশিষ্ট্য", "সুবিধা", "অসুবিধা", "প্রকারভেদ",
        "কীভাবে কাজ", "কীভাবে হয়",
        "what is", "why ", "explain", "definition", "describe", "difference",
    ]
    calc_signals = [
        "নির্ণয় কর", "সমাধান কর", "হিসাব কর", "বের কর", "প্রমাণ কর",
        "রূপান্তর কর", "সরল কর", "গণনা কর", "প্রয়োগ কর",
        "calculate", "solve", "find the", "prove", "simplify", "convert",
        "ভগ্নাংশে", "দশমিকে", "ল.সা.গু", "গ.সা.গু",
    ]

    has_theory = any(s in text for s in theory_signals)
    has_calc = any(s in text for s in calc_signals)

    if has_theory and has_calc:
        return "mixed"
    if has_calc or is_math_question(user_input):
        return "math"
    return "theory"


def route_model(user_input: str, preferred_model: str) -> str:
    """
    Smart routing: DeepSeek models (Flash + Pro) are only used for math questions.
    Theory questions route to Gemini 2.5 Flash for better Bengali prose quality.
    Returns the effective model name to use downstream.
    """
    if preferred_model in ("deepseek", "deepseek-pro"):
        if is_math_question(user_input):
            print(f"[Smart Route] Math detected → keeping {preferred_model}", flush=True)
            return preferred_model
        print(f"[Smart Route] Theory detected → Gemini 2.5 Flash (was {preferred_model})", flush=True)
        return "gemini"
    return preferred_model or "gemini"


def pick_chain(_user_input: str, subject: str = "", preferred_model: str = ""):
    """Selects the chain for the given effective model (post-routing)."""
    if preferred_model == "deepseek-pro":
        print(f"[Model] DeepSeek V4 Pro (Math Pro)", flush=True)
        return deepseek_pro_chain
    if preferred_model == "deepseek":
        print(f"[Model] DeepSeek V4 Flash (Math+)", flush=True)
        return deepseek_chain
    print(f"[Routing] {subject or 'general'} → Gemini 2.5 Flash", flush=True)
    return flash_chain


# ── TOC SHORT-CIRCUIT ──
TOC_KEYWORDS = [
    "chapter", "অধ্যায়", "তালিকা", "syllabus", "chapter gula", "কয়টি", "koyta",
]

SUBJECT_ALIASES = {
    "biology":     ["biology", "bio", "জীববিজ্ঞান", "জীব বিজ্ঞান", "জিববিজ্ঞান"],
    "geography":   ["geography", "geo", "bugol", "bhugol", "ভূগোল", "ভুগোল", "bugol o poribesh"],
    "accounting":  ["accounting", "হিসাববিজ্ঞান", "হিসাব", "hisoab", "account"],
    "physics":     ["physics", "পদার্থবিজ্ঞান", "পদার্থ", "পদার্থ বিজ্ঞান", "podartho", "podarthobiggyan"],
    "math":        ["math", "mathematics", "গণিত", "gonit", "algebra", "geometry", "বীজগণিত", "জ্যামিতি", "পরিমিতি", "পরিসংখ্যান", "ত্রিকোণমিতি"],
    "higher_math": ["higher math", "higher_math", "উচ্চতর গণিত", "উচ্চ গণিত", "uchcho gonit", "higher gonit", "h math", "hm"],
    "bangla":      [
        "bangla", "bangla sahitto", "বাংলা", "বাংলা সাহিত্য", "sahitto", "সাহিত্য", "bangla sahitya",
        # romanised piece titles — catches "bosek ke likesen", "ranar ki", etc.
        "bosek", "boshek", "ranar", "michil", "mochil", "subha", "momtadi",
        "nimgach", "abhagir", "chunia", "ekushey", "ekattorer", "bongobani",
        "kapotakkho", "juta abishkar", "jibonsongit", "manush muhammad",
        "tomake pawar", "shamsur", "sukanta", "al mahmud", "nazrul kobita",
        "bangla kobita", "kobita", "godyo", "goddo", "prose", "poem bangla",
    ],
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
        "নিউক্লিয়াস", "nucleus", "কেন্দ্রিকা", "সাইটোপ্লাজম", "cytoplasm",
        "রাইবোজোম", "ribosome", "ক্লোরোপ্লাস্ট", "chloroplast",
        "অসমোসিস", "osmosis", "অভিস্রবণ", "ব্যাপন", "diffusion",
        "পরিবহন", "উদ্দীপনা", "স্নায়ু", "nerve", "নিউরন", "neuron",
        "ফুলকা", "gill", "ফার্ন", "fern", "শৈবাল", "algae", "ছত্রাক", "fungi",
        "খাদ্যশৃঙ্খল", "food chain", "খাদ্যজাল", "food web",
        "রোগ", "disease", "প্রতিরোধ", "immunity", "অ্যান্টিবডি", "antibody",
        "হরমোন", "hormone", "এনজাইম", "enzyme",
    ],
    "physics": [
        "বেগ", "velocity", "ত্বরণ", "acceleration", "বল", "force",
        "ঘর্ষণ", "friction", "নিউটন", "newton", "ভরবেগ", "momentum",
        "বিদ্যুৎ", "electricity", "চুম্বক", "magnet", "তরঙ্গ", "wave",
        "শব্দ", "sound", "আলো", "light", "তাপ", "heat", "চাপ", "pressure",
        "ক্ষমতা", "power", "কাজ", "work", "শক্তি", "energy",
        "প্রতিসরণ", "refraction", "প্রতিফলন", "reflection",
        "ট্রান্সফর্মার", "transformer", "রোধ", "resistance", "বর্তনী", "circuit",
        "ভোল্টেজ", "voltage", "কারেন্ট", "current", "লেন্স", "lens",
        "মহাকর্ষ", "gravity", "পরমাণু", "atom", "তেজস্ক্রিয়", "radioactive",
        # additional SSC physics terms
        "দর্পণ", "mirror", "সরণ", "displacement", "গতি", "motion",
        "কম্পাঙ্ক", "frequency", "বিস্তার", "amplitude", "তরঙ্গদৈর্ঘ্য", "wavelength",
        "তড়িৎ", "electric", "চার্জ", "charge", "কুলম্ব", "coulomb",
        "ওহম", "ohm", "অ্যাম্পিয়ার", "ampere", "ওয়াট", "watt",
        "ফ্লেমিং", "fleming", "ডায়নামো", "dynamo", "মোটর", "motor",
        "দোলক", "pendulum", "স্থিতিস্থাপকতা", "elasticity",
        "আপেক্ষিক", "relative", "ঘনত্ব", "density", "প্লবতা", "buoyancy",
        "সান্দ্রতা", "viscosity", "পৃষ্ঠটান", "surface tension",
        "ক্যালোরি", "calorie", "তাপধারণ", "specific heat",
        "আলোকবিদ্যা", "optics", "বর্ণালি", "spectrum", "প্রিজম", "prism",
        "তড়িৎচুম্বক", "electromagnetic", "ফটোন", "photon",
        "নিউক্লিয়", "nuclear", "ফিশন", "fission", "ফিউশন", "fusion",
        "অর্ধপরিবাহী", "semiconductor", "ডায়োড", "diode", "ট্রানজিস্টর",
        "পদার্থবিজ্ঞান", "পদার্থ", "physics",
    ],
    "chemistry": [
        "পরমাণু", "atom", "অণু", "molecule", "রাসায়নিক", "chemical",
        "বিক্রিয়া", "reaction", "যৌগ", "compound", "মৌল", "element",
        "অ্যাসিড", "acid", "ক্ষার", "base", "লবণ", "salt",
        "ইলেকট্রন", "electron", "প্রোটন", "proton", "নিউট্রন", "neutron",
        "পর্যায় সারণি", "periodic table",
        "রসায়ন", "chemistry", "জারণ", "oxidation", "বিজারণ", "reduction",
        "তড়িৎ বিশ্লেষণ", "electrolysis", "গ্যাস", "gas", "বাষ্প", "vapour",
        "দ্রবণ", "solution", "দ্রাবক", "solvent", "দ্রাব্যতা", "solubility",
        "কার্বন", "carbon", "হাইড্রোজেন", "hydrogen", "অক্সিজেন", "oxygen",
        "নাইট্রোজেন", "nitrogen", "ধাতু", "metal", "অধাতু", "nonmetal",
        "সমযোজী", "covalent", "আয়নিক", "ionic", "হাইড্রোকার্বন", "hydrocarbon",
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
    "math": [
        "গণিত", "বীজগণিত", "জ্যামিতি", "ত্রিকোণমিতি", "পরিমিতি", "পরিসংখ্যান",
        "সমীকরণ", "equation", "সংখ্যা", "লগারিদম", "logarithm", "সূচক",
        "ত্রিভুজ", "triangle", "বৃত্ত", "circle", "কোণ", "angle",
        "অনুপাত", "ratio", "ধারা", "series", "ফাংশন", "function",
        "বর্গমূল", "sqrt", "উৎপাদক", "factor", "সম্ভাবনা", "probability",
        "গড়", "মধ্যক", "প্রচুরক", "mean", "median", "mode", "অজিভ",
    ],
    "higher_math": [
        "উচ্চতর গণিত", "higher math", "সেট", "set", "ফাংশন", "function",
        "বীজগাণিতিক রাশি", "বহুপদী", "polynomial", "ভাগশেষ উপপাদ্য", "উৎপাদক উপপাদ্য",
        "আংশিক ভগ্নাংশ", "partial fraction", "দ্বিঘাত সমীকরণ", "quadratic",
        "অসমতা", "inequality", "অসীম ধারা", "infinite series", "অভিসারী",
        "sin", "cos", "tan", "ত্রিকোণমিতিক সনাক্ত", "trigonometric identity",
        "সূচকীয়", "exponential", "লগারিদমীয়", "logarithmic",
        "দ্বিপদী বিস্তৃতি", "binomial", "স্থানাঙ্ক জ্যামিতি", "coordinate geometry",
        "ভেক্টর", "vector", "ঘন জ্যামিতি", "solid geometry", "গোলক", "sphere",
        "সম্ভাবনা", "probability", "স্বাধীন ঘটনা", "শর্তাধীন সম্ভাবনা",
        "যোগিক ফাংশন", "বিপরীত ফাংশন", "ভেনচিত্র", "venn",
    ],
    "bangla": [
        "বাংলা সাহিত্য", "গদ্য", "কবিতা", "ছোটগল্প", "প্রবন্ধ",
        "রবীন্দ্রনাথ", "tagore", "নজরুল", "nazrul", "জসীমউদদীন",
        "বিদ্যাসাগর", "vidyasagar", "বঙ্কিম", "শরৎচন্দ্র",
        "সুভা", "অভাগীর", "মমতাদি", "একাত্তর", "একুশের",
        "কবি", "poet", "লেখক", "writer", "উপন্যাস", "novel",
        "ছন্দ", "rhythm", "অলংকার", "metaphor", "উপমা", "simile",
        "প্রতুপকার", "নিমগাছ", "রানার", "সাম্যবাদী",
        "মুক্তিযুদ্ধ সাহিত্য", "bangla literature", "sahitto",
        # গদ্য titles
        "ফুলের বিবাহ", "লাইব্রেরি", "বই পড়া", "নিরীহ বাঙালি",
        "পল্লীসাহিত্য", "উদ্যম ও পরিশ্রম", "জীবনে শিল্পের",
        "আম-আঁটির", "মানুষ মুহম্মদ", "উপেক্ষিত শক্তির",
        "শিক্ষা ও মনুষ্যত্ব", "প্রবাস বন্ধু", "বনমানুষ",
        "স্বাধীনতা আমার স্বাধীনতা", "আমাদের সংস্কৃতি",
        "সাহিত্যের রূপ", "বাংলা শব্দ", "নতুন গৌরবগাথা",
        # কবিতা titles
        "বন্দনা", "হামদ", "বঙ্গবাণী", "কপোতাক্ষ", "জীবন-সঙ্গীত",
        "জুতা-আবিষ্কার", "ঝরনার গান", "ছায়াবাজি", "জীবন বিনিময়",
        "উমর ফারুক", "সেইদিন এই মাঠ", "যাব আমি তোমার দেশে",
        "একটি কবিতা", "আমার দেশ", "আমি কোনো আগন্তুক",
        "মে-দিনের", "পোস্টার", "তোমাকে পাওয়ার জন্যে",
        "অবাক সূর্যোদয়", "বোশেখ", "চুনিয়া", "মিছিল",
        # author names
        "প্রমথ চৌধুরী", "মোতাহের হোসেন", "সৈয়দ মুজতবা",
        "মানিক বন্দ্যোপাধ্যায়", "জাহানারা ইমাম", "জহির রায়হান",
        "আনিসুজ্জামান", "হুমায়ুন আজাদ", "আল মাহমুদ",
        "শামসুর রাহমান", "সুকান্ত", "রুদ্র মুহম্মদ",
        "রফিক আজাদ", "ফররুখ", "আহসান হাবীব", "সুফিয়া কামাল",
        "জীবনানন্দ", "সত্যেন্দ্রনাথ", "সুকুমার রায়",
        # romanised piece titles and author names
        "bosek", "boshek", "ranar", "michil", "subha", "momtadi", "nimgach",
        "abhagir", "chunia", "ekushey", "ekattorer", "al mahmud", "sukanta",
        "nazrul", "tagore", "kobita", "godyo", "sahitto", "bangla lit",
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
    # Emotional / wellbeing — no study chips for these
    "not feeling good", "not feeling well", "feeling bad", "feeling sick",
    "feeling sad", "feel bad", "feel sick", "feel sad", "im sad", "i'm sad",
    "i am sad", "not well", "don't want to study", "dont want to study",
    "not in mood", "no mood", "tired of studying", "ভালো লাগছে না",
    "মন খারাপ", "ভালো নেই", "শরীর ভালো না", "অসুস্থ", "ক্লান্ত",
    "পড়তে ইচ্ছা করছে না", "পড়তে ইচ্ছে করছে না", "পড়তে মন চাইছে না",
    "depressed", "anxious", "stressed", "stress", "tension",
    "worried", "upset", "lonely", "alone",
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
        "physics": "পদার্থবিজ্ঞান",
        "chemistry": "রসায়ন",
        "math": "গণিত",
        "higher_math": "উচ্চতর গণিত",
        "bangla": "বাংলা সাহিত্য",
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
    (['chemistry', 'chem', 'রসায়ন', 'rasayan'], 'chemistry'),
    (['accounting', 'account', 'হিসাব', 'acounting', 'accounts'], 'accounting'),
    (['geography', 'geo', 'ভূগোল', 'geograph'], 'geography'),
    (['higher math', 'higher_math', 'উচ্চতর গণিত', 'উচ্চ গণিত', 'uchcho gonit'], 'higher_math'),
    (['bangla sahitto', 'bangla sahitya', 'বাংলা সাহিত্য', 'sahitto', 'bangla lit'], 'bangla'),
    (['math', 'গণিত', 'gonit'], 'math'),
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
    has_study_session = len(study_messages) >= 2  # one Q&A exchange is enough

    # Guard: if conversation is mostly meta (about Dipti herself / app / subjects list),
    # not about actual subject matter — refuse to generate quiz
    if has_study_session:
        _META = ["দীপ্তি", "dipti", "কোন বিষয়", "কোন subject", "ki ki", "কী কী",
                 "kon kon", "কী পড়াও", "কী পড়ান", "app", "কে তৈরি", "কে বানিয়েছে"]
        _meta_hits = sum(
            1 for m in study_messages[-8:]
            if any(p.lower() in str(m.get('content', '')).lower() for p in _META)
        )
        if _meta_hits >= len(study_messages[-8:]) // 2:
            return {"no_topic": True, "reason": "meta"}

    convo = "\n".join(
        f"{'ছাত্র' if m['role']=='user' else 'দীপ্তি'}: {str(m.get('content',''))[:300]}"
        for m in study_messages[-12:]
    )

    book_context = ""

    if has_study_session:
        # Studying mode: quiz only from conversation, no RAG
        # Exhaust only after asking at least 5 questions AND covering as many questions as the student sent messages
        student_turns = sum(1 for m in study_messages if m['role'] == 'user')
        if len(asked) >= max(5, student_turns):
            return {"exhausted": True}
        content_block = f"ছাত্র এই session-এ যা পড়েছে:\n{convo}"
        source_rule = "শুধুমাত্র উপরের কথোপকথনে যা আলোচনা হয়েছে সেখান থেকে প্রশ্ন তৈরি করো — textbook থেকে নতুন কিছু আনবে না"
    else:
        # Cold start: no real study session, use RAG from user's topic query.
        # If chip trigger (no explicit query), extract topic from whatever history exists
        # so very fast chip clicks (before DB save) still produce a relevant quiz.
        effective_query = user_query.strip()
        if not effective_query:
            for m in reversed(history):
                content = str(m.get('content', '')).strip()
                if (m.get('role') == 'user'
                        and content
                        and content not in ('Quiz করো 🎯', 'বুঝেছি', 'পরবর্তী প্রশ্ন')
                        and not content.startswith('__')):
                    effective_query = content[:200]
                    break
        if not effective_query:
            return {"no_topic": True, "reason": "no_history"}
        try:
            from rag.query import get_relevant_chunks
            book_context = get_relevant_chunks(effective_query, subject=subject, top_k=5)
        except Exception as e:
            print(f"[quiz] RAG error: {e}")
        if not book_context:
            return {"no_topic": True, "reason": "no_study"}
        content_block = f"পাঠ্যপুস্তকের অংশ:\n{book_context}"
        source_rule = "উপরের পাঠ্যপুস্তকের অংশ থেকে একটি factual MCQ প্রশ্ন তৈরি করো"

    asked_block = ""
    if asked:
        asked_block = "\n\nইতিমধ্যে এই প্রশ্ন ও concept গুলো cover হয়েছে — একই concept ভিন্নভাবে জিজ্ঞেস করো না:\n" + "\n".join(f"- {q}" for q in asked)

    _subject_bn = {
        'biology': 'জীববিজ্ঞান', 'physics': 'পদার্থবিজ্ঞান', 'chemistry': 'রসায়ন',
        'math': 'গণিত', 'higher_math': 'উচ্চতর গণিত',
        'accounting': 'হিসাববিজ্ঞান', 'geography': 'ভূগোল',
        'bangla': 'বাংলা সাহিত্য',
    }.get(subject, subject)

    prompt = f"""তুমি SSC পরীক্ষার প্রশ্ন তৈরি করছ।

বিষয়: **{_subject_bn}** — শুধুমাত্র এই বিষয়ের প্রশ্ন তৈরি করো। অন্য বিষয় (জীববিজ্ঞান, পদার্থ, রসায়ন, ভূগোল, গণিত, হিসাব) থেকে প্রশ্ন করা যাবে না।

{content_block}{asked_block}

নিয়ম:
- {source_rule}
- প্রশ্নটি অবশ্যই **{_subject_bn}** বিষয়ের হবে — অন্য কোনো বিষয় থেকে নয়
- Dipti AI, এই app, বা "দীপ্তি আপু কী পড়ান" ধরনের কোনো প্রশ্ন করবে না — এটা quiz নয়
- প্রতিটি প্রশ্ন আলাদা concept ও আলাদা fact cover করবে
- আগের প্রশ্নে যে concept, term বা fact ছিল — সেটা ভিন্নভাবেও জিজ্ঞেস করবে না
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
        "subjects": ["biology", "physics", "chemistry", "math", "higher_math", "bangla"],
        "label": "Biology, Physics, Chemistry, Math, Higher Math, Bangla Sahitto",
    },
    "commerce": {
        "name": "ব্যবসায় শিক্ষা বিভাগ",
        "subjects": ["accounting", "economics", "bangla"],
        "label": "Accounting, Economics, Finance & Banking, Business Studies, Bangla Sahitto",
    },
    "arts": {
        "name": "মানবিক বিভাগ",
        "subjects": ["geography", "history", "civics", "bangla"],
        "label": "Geography, History, Civics, Economics, Bangla Sahitto",
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
        "math": "গণিত", "higher_math": "উচ্চতর গণিত",
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


_NAME_CORRECTION_RE = re.compile(
    r'(?:'
    r'(?:amr|amar|ami|my)\s+name\s+(?:is\s+)?(\w+)'       # "amr name libo" / "my name is libo"
    r'|name\s+(\w+)\s+not'                                   # "name libo not ibo"
    r'|আমার\s+নাম\s+([^\s।,.!?]+)'                         # "আমার নাম লিবো"
    r'|নাম\s+([^\s।,.!?]+)\s*[,।]?\s*(?:ভুল|নয়|না|কিন্তু)' # "নাম বসন্ত, ভুল"
    r')',
    re.IGNORECASE
)

def detect_name_correction(message: str) -> str | None:
    """Returns the corrected name if the student is correcting their name, else None."""
    m = _NAME_CORRECTION_RE.search(message)
    if m:
        name = next((g for g in m.groups() if g), None)
        if name:
            name = name.strip().rstrip('.!?,।')
            if len(name) >= 2:
                return name
    return None


def build_system_prompt(nctb_context: str, project_instructions: str = "", stream: str = "", student_name: str = "", student_profile: dict = None) -> str:
    """Build the full system prompt with NCTB context + optional project instructions."""
    prompt = SYSTEM_PROMPT

    if student_name and student_name.strip():
        first_name = student_name.strip().split()[0]
        bangla_name = _transliterate_name_to_bangla(first_name)
        prompt += f"""

## ছাত্রের নাম: {bangla_name}

নাম ব্যবহারের নিয়ম:
- প্রথম উত্তরে নাম দিয়ে শুরু করো: "শোনো {bangla_name}," বা "{bangla_name}, চল দেখি —"
- এরপর প্রতি ৩-৪ বার্তায় একবার ব্যবহার করো — প্রশংসা করার সময়, নতুন topic শুরুতে, বা গুরুত্বপূর্ণ কিছু বলার আগে
- উদাহরণ: "বাহ {bangla_name}!", "মনে রাখো {bangla_name},", "শোনো {bangla_name},"
⚠️ HARD RULE: নাম লেখার সময় শুধু "{bangla_name}" লিখবে — কখনো "{first_name}" বা অন্য কোনো Latin/English script-এ লিখবে না।
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


# Romanised spellings → canonical Bengali piece title for bangla RAG fallback.
# Covers common student transliterations. Keys are lowercase stripped.
_BANGLA_ROMAN_TO_BN = {
    # কবিতা
    "bosek": "বোশেখ", "boshek": "বোশেখ", "boisakh": "বোশেখ", "boishakh": "বোশেখ",
    "ranar": "রানার", "raanar": "রানার",
    "michil": "মিছিল", "mochil": "মিছিল", "mishil": "মিছিল",
    "chunia": "চুনিয়া", "chuniya": "চুনিয়া",
    "manush": "মানুষ", "manus": "মানুষ",
    "praan": "প্রাণ", "pran": "প্রাণ",
    "bandana": "বন্দনা", "bondona": "বন্দনা",
    "hamd": "হামদ্", "hamdh": "হামদ্",
    "bongobani": "বঙ্গবাণী", "bangobani": "বঙ্গবাণী",
    "kapotakkho": "কপোতাক্ষ", "kopotokkho": "কপোতাক্ষ",
    "jiban songit": "জীবন-সঙ্গীত", "jibonsongit": "জীবন-সঙ্গীত",
    "juta abishkar": "জুতা-আবিষ্কার", "juta aviskar": "জুতা-আবিষ্কার",
    "jhorna": "ঝরনার গান", "jhornar gan": "ঝরনার গান",
    "chhayabaji": "ছায়াবাজি", "chayabaji": "ছায়াবাজি",
    "umar faruk": "উমর ফারুক", "omar faruk": "উমর ফারুক",
    "shamsur": "তোমাকে পাওয়ার", "tomake pawar": "তোমাকে পাওয়ার",
    "poster": "পোস্টার",
    "asha": "আশা", "aasha": "আশা",
    "bristi": "বৃষ্টি", "brishti": "বৃষ্টি",
    "meyedin": "মে-দিনের", "me diner": "মে-দিনের",
    "obak": "অবাক সূর্যোদয়", "abak": "অবাক সূর্যোদয়",
    "seidin": "সেইদিন", "sheidin": "সেইদিন",
    "sammobadi": "সাম্যবাদী", "samobadi": "সাম্যবাদী",
    # গদ্য
    "subha": "সুভা", "suva": "সুভা",
    "abhagir": "অভাগীর স্বর্গ", "obhagir": "অভাগীর স্বর্গ",
    "momtadi": "মমতাদি", "momtadee": "মমতাদি",
    "nimgach": "নিমগাছ", "nimgaach": "নিমগাছ",
    "am atir": "আম-আঁটির ভেঁপু", "amattir": "আম-আঁটির ভেঁপু",
    "bonomanush": "বনমানুষ", "bono manush": "বনমানুষ",
    "ekattorer": "একাত্তরের দিনগুলি", "71 er": "একাত্তরের দিনগুলি",
    "ekushey": "একুশের গল্প", "ekusher": "একুশের গল্প",
    "sikkha": "শিক্ষা ও মনুষ্যত্ব", "shikkha": "শিক্ষা ও মনুষ্যত্ব",
    "boi pora": "বই পড়া", "boi para": "বই পড়া",
    "library": "লাইব্রেরি", "laibrary": "লাইব্রেরি",
}


# Authoritative title → author mapping. Used to inject verified author info
# BEFORE RAG chunks so MCQ wrong-answer options cannot override it.
_BANGLA_AUTHORS = {
    "প্রতুপকার": "ঈশ্বরচন্দ্র বিদ্যাসাগর", "ফুলের বিবাহ": "বঙ্কিমচন্দ্র চট্টোপাধ্যায়",
    "সুভা": "রবীন্দ্রনাথ ঠাকুর", "লাইব্রেরি": "রবীন্দ্রনাথ ঠাকুর",
    "বই পড়া": "প্রমথ চৌধুরী", "অভাগীর স্বর্গ": "শরৎচন্দ্র চট্টোপাধ্যায়",
    "নিরীহ বাঙালি": "রোকেয়া সাখাওয়াত হোসেন", "পল্লীসাহিত্য": "মুহম্মদ শহীদুল্লাহ",
    "উদ্যম ও পরিশ্রম": "মোহাম্মদ লুৎফর রহমান", "জীবনে শিল্পের স্থান": "এস. ওয়াজেদ আলি",
    "আম-আঁটির ভেঁপু": "বিভূতিভূষণ বন্দ্যোপাধ্যায়", "মানুষ মুহম্মদ": "মোহাম্মদ ওয়াজেদ আলী",
    "উপেক্ষিত শক্তির উদ্বোধন": "কাজী নজরুল ইসলাম", "নিমগাছ": "বনফুল (বলাইচাঁদ মুখোপাধ্যায়)",
    "শিক্ষা ও মনুষ্যত্ব": "মোতাহের হোসেন চৌধুরী", "প্রবাস বন্ধু": "সৈয়দ মুজতবা আলী",
    "মমতাদি": "মানিক বন্দ্যোপাধ্যায়", "বনমানুষ": "আবু ইসহাক",
    "একাত্তরের দিনগুলি": "জাহানারা ইমাম", "স্বাধীনতা আমার স্বাধীনতা": "মমতাজউদদীন আহমদ",
    "একুশের গল্প": "জহির রায়হান", "আমাদের সংস্কৃতি": "আনিসুজ্জামান",
    "সাহিত্যের রূপ ও রীতি": "হায়াৎ মামুদ", "বাংলা শব্দ": "হুমায়ুন আজাদ",
    "বন্দনা": "শাহ মুহম্মদ সগীর", "হামদ্": "আলাওল",
    "বঙ্গবাণী": "আবদুল হাকিম", "কপোতাক্ষ নদ": "মাইকেল মধুসূদন দত্ত",
    "জীবন-সঙ্গীত": "হেমচন্দ্র বন্দ্যোপাধ্যায়", "প্রাণ": "রবীন্দ্রনাথ ঠাকুর",
    "জুতা-আবিষ্কার": "রবীন্দ্রনাথ ঠাকুর", "ঝরনার গান": "সত্যেন্দ্রনাথ দত্ত",
    "ছায়াবাজি": "সুকুমার রায়", "জীবন বিনিময়": "গোলাম মোস্তফা",
    "সাম্যবাদী": "কাজী নজরুল ইসলাম",
    "মানুষ": "কাজী নজরুল ইসলাম", "উমর ফারুক": "কাজী নজরুল ইসলাম",
    "সেইদিন এই মাঠ": "জীবনানন্দ দাশ", "যাব আমি তোমার দেশে": "জসীমউদ্দীন",
    "একটি কবিতা": "বিষ্ণু দে", "আমার দেশ": "সুফিয়া কামাল",
    "আমি কোনো আগন্তুক নই": "আহসান হাবীব", "বৃষ্টি": "ফররুখ আহমদ",
    "মে-দিনের কবিতা": "সুভাষ মুখোপাধ্যায়", "আশা": "সিকান্দার আবু জাফর",
    "পোস্টার": "আবুল হোসেন", "রানার": "সুকান্ত ভট্টাচার্য",
    "তোমাকে পাওয়ার জন্যে, হে স্বাধীনতা": "শামসুর রাহমান",
    "অবাক সূর্যোদয়": "হাসান হাফিজুর রহমান", "বোশেখ": "আল মাহমুদ",
    "চুনিয়া আমার আর্কেডিয়া": "রফিক আজাদ", "মিছিল": "রুদ্র মুহম্মদ শহিদুল্লাহ",
}


def _bangla_roman_lookup(text: str) -> str:
    """Return Bengali piece title if a known romanised name is found in text."""
    t = text.lower().strip()
    for roman, bn in _BANGLA_ROMAN_TO_BN.items():
        if roman in t:
            return bn
    return ""


def _bangla_author_hint(text: str) -> str:
    """
    If text mentions a known bangla piece title (Bengali or romanised),
    return a pinned author fact string to prepend to RAG context.
    """
    t = text.lower().strip()

    # Check Bengali titles first
    for title, author in _BANGLA_AUTHORS.items():
        if title in text:
            return (
                f"[✅ নিশ্চিত তথ্য — NCTB বাংলা সাহিত্য]\n"
                f"'{title}' → NCTB পাঠ্যবইয়ের একটি রচনার শিরোনাম। লেখক/কবি: {author}\n"
                f"(এই তথ্যটি সর্বোচ্চ অগ্রাধিকার — অন্য কোনো তথ্য থাকলেও এটাই সঠিক)"
            )

    # Then check romanised → Bengali title
    roman_title = _bangla_roman_lookup(text)
    if roman_title and roman_title in _BANGLA_AUTHORS:
        author = _BANGLA_AUTHORS[roman_title]
        # Find which romanised key triggered the match — used to bridge it for the LLM
        matched_roman = next(
            (r for r, bn in _BANGLA_ROMAN_TO_BN.items() if bn == roman_title and r in t),
            ""
        )
        bridge = (
            f"ছাত্র '{matched_roman}' বলতে NCTB পাঠ্যবইয়ের '{roman_title}' রচনা বোঝাচ্ছে।\n"
            if matched_roman else ""
        )
        return (
            f"[✅ নিশ্চিত তথ্য — NCTB বাংলা সাহিত্য]\n"
            f"{bridge}"
            f"'{roman_title}' রচনাটির লেখক/কবি: {author}\n"
            f"(এই তথ্যটি সর্বোচ্চ অগ্রাধিকার — অন্য কোনো তথ্য থাকলেও এটাই সঠিক)"
        )

    return ""


def do_rag_lookup(user_input: str, subject: str = "biology"):
    """
    Step 1: Run RAG retrieval ONLY. Returns (nctb_context, chapters_found).
    Separated so we can show progress to the user before the LLM call starts.
    """
    # Mixed-script queries (Bangla + romanised Latin) produce poor embeddings.
    # Use only the Bengali portion when it's long enough to be meaningful.
    bn_only = " ".join(re.findall(r"[ঀ-৿]+", user_input))

    # For bangla: if no Bengali text, try romanised piece title lookup → use the
    # Bengali title as the RAG query so the embedding matches the stored chunks.
    if subject == "bangla" and len(bn_only) < 3:
        bn_title = _bangla_roman_lookup(user_input)
        rag_query = bn_title if bn_title else user_input
    else:
        rag_query = bn_only if len(bn_only) >= 3 else user_input

    nctb_context = get_relevant_chunks(rag_query, subject=subject)
    chapters_found = get_chapters_for_question(rag_query, subject=subject)

    # For bangla: prepend a pinned author fact so MCQ wrong options cannot override it
    if subject == "bangla":
        hint = _bangla_author_hint(user_input)
        if hint:
            nctb_context = hint + ("\n\n---\n\n" + nctb_context if nctb_context else "")
            if not chapters_found:
                # extract piece name from hint for chapter label
                for title in _BANGLA_AUTHORS:
                    if title in user_input or (rag_query and title in rag_query):
                        chapters_found = [title]
                        break

    return nctb_context, chapters_found


def run_llm(user_input, history, nctb_context, project_instructions="", stream="", student_name="", subject="", student_profile: dict = None, preferred_model: str = ""):
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
    has_image = False
    vision_supported = preferred_model not in ("deepseek", "deepseek-pro")
    for msg in history:
        if msg["role"] == "user":
            img_url = msg.get("image_url")
            if img_url and vision_supported:
                has_image = True
                messages.append(HumanMessage(content=[
                    {"type": "image_url", "image_url": {"url": img_url}},
                    {"type": "text", "text": msg["content"] or "এই ছবিটি দেখে বুঝিয়ে দাও।"},
                ]))
            elif img_url and not vision_supported:
                if img_url in _img_extract_cache:
                    print("[Hybrid] Cache hit — skipping re-extraction", flush=True)
                    extracted = _img_extract_cache[img_url]
                else:
                    print("[Hybrid] Gemini extracting image for DeepSeek…", flush=True)
                    try:
                        extracted = vision_chain.invoke([HumanMessage(content=[
                            {"type": "image_url", "image_url": {"url": img_url}},
                            {"type": "text", "text": "ছবিতে যা আছে সম্পূর্ণ text-এ লেখো — সব প্রশ্ন, তথ্য, সংখ্যা, diagram description।"},
                        ])])
                        _img_extract_cache[img_url] = extracted
                    except Exception:
                        extracted = msg.get("content") or "[ছবি পড়া যায়নি]"
                text = f"[ছবির content:]\n{extracted}\n\n{msg.get('content', '')}".strip()
                messages.append(HumanMessage(content=text))
            else:
                messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_input))

    # Image in history: Gemini Flash extracts problem → Gemini Flash solves → Flash Lite rewrites
    if has_image:
        print("[Image Pipeline] Step 1: Gemini extracts problem from image")
        extract_msgs = messages[:-1] + [HumanMessage(content=(
            "ছবিতে যা আছে সম্পূর্ণ text-এ লেখো — সব প্রশ্ন, তথ্য, সংখ্যা। "
            "শুধু extract করো, কোনো solution দেবে না।"
        ))]
        extracted = vision_chain.invoke(extract_msgs)

        # Strip images from history, rebuild with extracted text for Gemini Flash
        text_messages = []
        for msg in messages[:-1]:
            if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
                text_parts = [p.get("text", "") for p in msg.content
                              if isinstance(p, dict) and p.get("type") == "text"]
                text_messages.append(HumanMessage(content="\n".join(filter(None, text_parts)) or "[image]"))
            else:
                text_messages.append(msg)
        text_messages.append(HumanMessage(content=(
            f"[ছবির সমস্যা:]\n{extracted}\n\n[ছাত্রের নির্দেশ:] {user_input}"
        )))

        selected = pick_chain(user_input, subject=subject)
        label = "Flash Lite rewrite" if selected is two_step_output_chain else "Flash direct"
        print(f"[Image Pipeline] Step 2: Gemini Flash solves -> {label}")
        return selected.invoke(text_messages)

    effective_model = route_model(user_input, preferred_model)
    selected_chain = pick_chain(user_input, subject=subject, preferred_model=effective_model)
    return selected_chain.invoke(messages)


def _build_chat_messages(system_text: str, history: list, user_input: str, vision_supported: bool) -> list:
    """Build a full LangChain messages list from history, handling image extraction for DeepSeek."""
    messages = [SystemMessage(content=[{
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }])]
    for msg in history:
        if msg["role"] == "user":
            img_url = msg.get("image_url")
            if img_url and vision_supported:
                messages.append(HumanMessage(content=[
                    {"type": "image_url", "image_url": {"url": img_url}},
                    {"type": "text", "text": msg["content"] or "এই ছবিটি দেখে বুঝিয়ে দাও।"},
                ]))
            elif img_url and not vision_supported:
                if img_url in _img_extract_cache:
                    extracted = _img_extract_cache[img_url]
                else:
                    try:
                        print("[Hybrid] Gemini extracting image for DeepSeek…", flush=True)
                        extracted = vision_chain.invoke([HumanMessage(content=[
                            {"type": "image_url", "image_url": {"url": img_url}},
                            {"type": "text", "text": "ছবিতে যা আছে সম্পূর্ণ text-এ লেখো — সব প্রশ্ন, তথ্য, সংখ্যা, diagram description।"},
                        ])])
                        _img_extract_cache[img_url] = extracted
                    except Exception:
                        extracted = msg.get("content") or "[ছবি পড়া যায়নি]"
                text = f"[ছবির content:]\n{extracted}\n\n{msg.get('content', '')}".strip()
                messages.append(HumanMessage(content=text))
            else:
                messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_input))
    return messages


def stream_llm(user_input, history, nctb_context, project_instructions="", stream="", student_name="", subject="", student_profile=None, preferred_model: str = ""):
    """
    Streaming version of run_llm.
    For DeepSeek models with mixed questions, streams Gemini (theory) then DeepSeek (math).
    """
    # Redirect retired model to its replacement
    if preferred_model == "deepseek-pro":
        preferred_model = "deepseek"
    base_system = build_system_prompt(nctb_context, project_instructions, stream=stream, student_name=student_name, student_profile=student_profile)

    _latex_core = (
        "CRITICAL LaTeX RULES — violating these breaks rendering:\n"
        "1. NEVER nest $...$ inside $$...$$. Write LaTeX directly inside display blocks — no inner $ signs.\n"
        "   ✗ WRONG: $$x = $0.\\overline{3}$ = 0.333...$\n"
        "   ✓ CORRECT: $$x = 0.\\overline{3} = 0.333...$$\n"
        "2. NEVER put $ signs inside {} argument braces.\n"
        "   ✗ WRONG: \\frac{$0.\\overline{78}$}{100}   ✓ CORRECT: \\frac{0.\\overline{78}}{100}\n"
        "3. Each step equation = ONE $$...$$ block on its own line. Never split across multiple $$ blocks.\n"
        "   ✗ WRONG: $$x$$ $$=$$ $$\\frac{1}{3}$$   ✓ CORRECT: $$x = \\frac{1}{3}$$\n"
        "4. NEVER put $ inside {} braces: ✗ \\boxed{$x$}  ✓ \\boxed{x}\n\n"
    )
    _gemini_rules = (
        "CRITICAL LaTeX RULES for recurring decimals and display math:\n"
        "1. Use ONLY \\overline{} for recurring decimals — NEVER \\dot{} and NEVER Unicode ˙ character.\n"
        "   ✗ WRONG: $0.\\dot{3}$  or  0.3˙\n"
        "   ✓ CORRECT: $0.\\overline{3}$\n"
        "2. Use ONE \\overline{} for the ENTIRE recurring block — NEVER separate \\overline{} per digit.\n"
        "   ✗ WRONG: $0.\\overline{2}\\overline{4}$  or  $42.34\\overline{7}\\overline{8}$\n"
        "   ✓ CORRECT: $0.\\overline{24}$  and  $42.34\\overline{78}$\n"
        "3. Write each number/equation EXACTLY ONCE — in LaTeX only. Never write the same value twice.\n"
        "4. NEVER nest $...$ inside $$...$$. No inner $ signs inside display blocks.\n"
        "5. Each step equation = ONE $$...$$ block on its own line.\n"
        "6. ALWAYS use Arabic/English numerals (0-9) in chemical equations — NEVER Bengali digits (০-৯).\n"
        "   ✗ WRONG: ৬CO₂ + ৬H₂O → গ্লুকোজ + ৬O₂\n"
        "   ✓ CORRECT: 6CO₂ + 6H₂O → গ্লুকোজ + 6O₂\n"
        "7. 'ধরি' STEP — ONE $...$ inline block. The variable name appears ONCE. Never split:\n"
        "   ✗ WRONG: ধরি, $x$ = x=0.\\overline{3}   ← $x$ alone, then x= repeated in text!\n"
        "   ✗ WRONG: ধরি, $$x$$ = $$x=0.\\overline{3}$$  ← split into multiple display blocks!\n"
        "   ✗ WRONG: ধরি, x = $0.\\overline{3}$   ← x= in plain text before the $ block\n"
        "   ✓ CORRECT: ধরি, $x = 0.\\overline{3}$  ← full equation in ONE inline $...$ block\n"
        "8. Equation labels (1),(2) go INSIDE $$...$$ — NEVER as separate trailing text:\n"
        "   ✗ WRONG: $$x = 0.333...$$ (1)   ← label outside block causes render split\n"
        "   ✓ CORRECT: $$x = 0.333...$$ then just reference 'উপরের সমীকরণ' in Bengali\n"
        "   Simplest: drop the (1)/(2) labels entirely — use step names like 'এখন (2) থেকে (1) বিয়োগ করি:'\n"
        "9. PROOF steps — intermediate result in text is FORBIDDEN:\n"
        "   ✗ WRONG: এখানে a=\\sqrt{1+x}+\\sqrt{1-x}, c=p এবং d=1।\n"
        "   ✓ CORRECT: এখানে $a = \\sqrt{1+x}+\\sqrt{1-x}$, $c = p$, $d = 1$।\n"
        "10. MANDATORY EXPLANATION RULE — Before EVERY $$...$$ equation block, write 1-2 Bengali sentences explaining WHAT you are doing and WHY. A student must understand each step without a teacher.\n"
        "   ✗ WRONG: $$2(lb+bh+hl) = 262$$\n$$lb+bh+hl = 131$$\n"
        "   ✓ CORRECT: প্রশ্নমতে, বাক্সের মোট পৃষ্ঠের ক্ষেত্রফল 262 বর্গ সে.মি.:\n$$2(lb+bh+hl) = 262$$\nএখন উভয় পক্ষকে 2 দিয়ে ভাগ করি:\n$$lb+bh+hl = 131$$\n\n"
    )
    _deepseek_rules = (
        "CRITICAL: Respond ONLY in Bengali (বাংলা). Never use Chinese characters. NEVER use Cyrillic/Russian script (а б в г etc.) — not even one letter. Use Bengali for all words including math steps like 'সরলীকরণ করো' (simplify), 'লঘিষ্ঠ আকারে' (reduce).\n\n"
        "CRITICAL LaTeX RULES:\n"
        "1. NEVER use \\begin{aligned}...\\end{aligned}. Write each equation step as its own $$...$$ line.\n"
        "2. NEVER nest $...$ inside $$...$$. No inner $ signs inside display blocks.\n"
        "3. Each step = ONE $$...$$ block on its own line. No split equations.\n"
        "4. NEVER put $ inside {} braces.\n"
        "5. Use \\overline{} for recurring decimals: $0.\\overline{3}$, $42.34\\overline{78}$\n"
        "6. ALWAYS use Arabic/English numerals (0-9) in chemical equations — NEVER Bengali digits.\n"
        "7. NEVER echo an equation in plain text after writing it in $$...$$. Write each equation EXACTLY ONCE — only inside $$...$$.\n"
        "   ✗ WRONG: $$10x = 3.3333...$$ then next line: 10x=3.3333...\n"
        "   ✓ CORRECT: $$10x = 3.3333...$$ then next line: Bengali explanation only\n"
        "8. 'ধরি' step — ONE $...$ inline block, variable name written ONCE:\n"
        "   ✗ WRONG: ধরি, $$x$$ = $$x=0.\\overline{3}$$ = $$0.3333...$$\n"
        "   ✗ WRONG: ধরি, x = $0.\\overline{3}$\n"
        "   ✓ CORRECT: ধরি, $x = 0.\\overline{3}$\n"
        "9. NEVER end ANY line with a lone trailing $. This applies to ALL line types:\n"
        "   ✗ WRONG: সহজ কথায়: $0.\\overline{3} = \\frac{1}{3}$ $\n"
        "   ✗ WRONG: 1. $0.\\overline{3} = \\frac{1}{3}$ $\n"
        "   ✓ CORRECT: 1. $0.\\overline{3} = \\frac{1}{3}$\n\n"
    )

    # Build per-model system strings
    gemini_sys    = _gemini_rules + base_system
    deepseek_sys  = _deepseek_rules + base_system
    deepseek_pro_sys = (
        "CRITICAL: Respond ONLY in Bengali (বাংলা). Never use Chinese or Cyrillic characters.\n\n"
        + _latex_core + base_system
    )

    # Classify question for hybrid routing
    if preferred_model in ("deepseek", "deepseek-pro"):
        q_type = classify_question(user_input)
    else:
        q_type = "theory"

    print(f"[Router] preferred={preferred_model or 'gemini'}, q_type={q_type}", flush=True)

    if preferred_model in ("deepseek", "deepseek-pro") and q_type == "mixed":
        # ── HYBRID MODE ──
        # Phase 1: Gemini explains the theory component
        # Phase 2: DeepSeek solves the math component
        theory_note = (
            "\n\nIMPORTANT: এই প্রশ্নে theory এবং math দুটো অংশ আছে। "
            "শুধু conceptual/theory অংশটুকু explain করো — কোনো equation solve বা "
            "step-by-step গাণিতিক calculation করো না।"
        )
        math_note = (
            "\n\nIMPORTANT: এই প্রশ্নের theory/explanation অংশ ইতিমধ্যে দেওয়া হয়েছে। "
            "শুধু গাণিতিক calculation/equation step-by-step solve করো — "
            "theory বা সংজ্ঞা repeat করো না।"
        )

        gemini_msgs = _build_chat_messages(gemini_sys + theory_note, history, user_input, vision_supported=True)

        if preferred_model == "deepseek-pro":
            ds_sys   = deepseek_pro_sys + math_note
            ds_chain = deepseek_pro_chain
            print("[Router] Hybrid: Gemini (theory) + DeepSeek V4 Pro (math)", flush=True)
        else:
            ds_sys   = deepseek_sys + math_note
            ds_chain = deepseek_chain
            print("[Router] Hybrid: Gemini (theory) + DeepSeek V4 Flash (math)", flush=True)

        ds_msgs = _build_chat_messages(ds_sys, history, user_input, vision_supported=False)

        # Stream theory phase
        for chunk in flash_chain.stream(gemini_msgs):
            if chunk:
                yield chunk

        # Separator before math section
        yield "\n\n**গাণিতিক সমাধান:**\n\n"

        # Stream math phase
        for chunk in ds_chain.stream(ds_msgs):
            if chunk:
                yield chunk

    elif preferred_model in ("deepseek", "deepseek-pro") and q_type == "math":
        # ── PURE MATH → DeepSeek only ──
        if preferred_model == "deepseek-pro":
            sys_text = deepseek_pro_sys
            ds_chain = deepseek_pro_chain
            print("[Router] Pure math → DeepSeek V4 Pro", flush=True)
        else:
            sys_text = deepseek_sys
            ds_chain = deepseek_chain
            print("[Router] Pure math → DeepSeek V4 Flash", flush=True)
        msgs = _build_chat_messages(sys_text, history, user_input, vision_supported=False)
        for chunk in ds_chain.stream(msgs):
            if chunk:
                yield chunk

    else:
        # ── THEORY ONLY or Gemini selected → Gemini ──
        if preferred_model in ("deepseek", "deepseek-pro"):
            print("[Router] Theory detected → Gemini 2.5 Flash (overrides DeepSeek)", flush=True)
        else:
            print(f"[Router] Gemini 2.5 Flash (subject={subject or 'general'})", flush=True)
        msgs = _build_chat_messages(gemini_sys, history, user_input, vision_supported=True)
        for chunk in flash_chain.stream(msgs):
            if chunk:
                yield chunk


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
            print(f"[TOC] Direct response for subject={detected_subject}")
            return {
                "reply": toc_reply,
                "chapters_found": ["অধ্যায় তালিকা (Table of Contents)"]
            }

    # Normal flow continues below
    t0 = time.time()
    nctb_context, chapters_found = do_rag_lookup(user_input, subject=detected_subject)

    print(f"[DEBUG] User asked: {user_input}")
    print(f"[DEBUG] Detected subject: {detected_subject}")
    print(f"[DEBUG] Chapters found: {chapters_found}")
    print(f"[DEBUG] Context length: {len(nctb_context)} chars")

    t1 = time.time()
    reply = run_llm(user_input, history, nctb_context, project_instructions, subject=detected_subject)
    t2 = time.time()

    print(f"RAG: {t1-t0:.2f}s | LLM: {t2-t1:.2f}s | TOTAL: {t2-t0:.2f}s")

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