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

# Patterns that signal a multi-step problem needing Pro
COMPLEX_MATH_KEYWORDS = [
    # Accounting — always multi-step
    "জাবেদা", "খতিয়ান", "রেওয়ামিল", "বিবরণী",
    "balance sheet", "trial balance", "ledger", "journal",
    "প্রাপ্তি ও প্রদান", "আয়-ব্যয়", "আর্থিক অবস্থা",
    # Explicit multi-step signals
    "ধাপে ধাপে", "step by step", "সমাধান কর", "প্রমাণ কর",
    "প্রস্তুত কর", "তৈরি কর",
    # Physics calculations
    "বেগ", "ত্বরণ", "বল", "কাজ", "ক্ষমতা", "শক্তি", "ভরবেগ",
    "তরঙ্গদৈর্ঘ্য", "কম্পাঙ্ক", "প্রতিরোধ", "তড়িৎ",
    # Chemistry
    "মোলার", "মোল", "stoichiometry", "বিক্রিয়া সমীকরণ",
    # Multi-equation / algebra
    "সমীকরণ সমাধান", "simultaneous", "দুটি সমীকরণ",
]

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

    # Count numbers in the text
    bangla_num_count = sum(1 for c in user_input if c in BANGLA_DIGITS)
    english_num_count = sum(1 for c in user_input if c in ENGLISH_DIGITS)
    total_digit_chars = bangla_num_count + english_num_count

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


def pick_chain(user_input: str):
    """Select which chain (LLM) to use based on question type."""
    if is_complex_math(user_input):
        print(f"🧮 [Routing] Complex math → Gemini 2.5 Pro")
        return gemini_pro_chain
    print(f"💬 [Routing] Theory/simple → Gemini 2.5 Flash")
    return flash_chain


# ── TOC SHORT-CIRCUIT ──
TOC_KEYWORDS = [
    "chapter", "অধ্যায়", "তালিকা", "syllabus",
    "chapter gula", "kon kon", "ki ki", "kiki", "koyta", "কয়টি",
]

SUBJECT_ALIASES = {
    "biology": ["biology", "bio", "জীববিজ্ঞান", "জীব বিজ্ঞান", "জিববিজ্ঞান"],
    "geography": ["geography", "geo", "bugol", "bhugol", "ভূগোল", "ভুগোল", "bugol o poribesh"],
    "accounting": ["accounting", "হিসাববিজ্ঞান", "হিসাব", "hisoab", "account"],
    "physics": ["physics", "পদার্থবিজ্ঞান", "পদার্থ", "পদার্থ বিজ্ঞান", "podartho", "podarthobiggyan"],
}


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
    for ordinal, (num, title) in chapters.items():
        chapter_lines.append(f"{num}. {title}")

    body = "\n".join(chapter_lines)

    outro = "\n\nকোন অধ্যায়টা সম্পর্কে জানতে চাও? বললেই বুঝিয়ে দিচ্ছি 🌸"

    return intro + body + outro


def build_system_prompt(nctb_context: str, project_instructions: str = "") -> str:
    """Build the full system prompt with NCTB context + optional project instructions."""
    prompt = SYSTEM_PROMPT

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


def run_llm(user_input, history, nctb_context, project_instructions=""):
    """
    Run the LLM with already-retrieved context. Auto-picks Flash or Haiku.
    Returns the final reply string.
    """
    system_with_context = build_system_prompt(nctb_context, project_instructions)
    messages = [SystemMessage(content=system_with_context)]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_input))

    # ── ROUTING happens here ──
    selected_chain = pick_chain(user_input)
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
    reply = run_llm(user_input, history, nctb_context, project_instructions)
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