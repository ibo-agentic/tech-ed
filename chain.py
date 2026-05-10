from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv
from prompt import SYSTEM_PROMPT
import os
import sys
import time

# Add rag/ folder to path
sys.path.append(os.path.join(os.path.dirname(__file__), "rag"))
from query import get_relevant_chunks, get_chapters_for_question
from chapters import CHAPTERS

load_dotenv()

llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.7
)
parser = StrOutputParser()
chain = llm | parser


# Subject -> ordinal -> (number, title)
TOC_KEYWORDS = [
    "chapter", "অধ্যায়", "তালিকা", "syllabus",
    "chapter gula", "kon kon", "ki ki", "koyta", "কয়টি"
]

SUBJECT_ALIASES = {
    "biology": ["biology", "bio", "জীববিজ্ঞান", "জীব বিজ্ঞান", "জিববিজ্ঞান"],
    "geography": ["geography", "geo", "bugol", "bhugol", "ভূগোল", "ভুগোল", "bugol o poribesh"],
}


def is_toc_question(user_input: str) -> bool:
    """Detect if user is asking 'list all chapters' type questions."""
    text = user_input.lower()
    has_chapter_word = any(kw in text for kw in TOC_KEYWORDS)
    # Must mention chapter/অধ্যায় AND ask a list-type question
    asks_list = any(w in text for w in ["ki ki", "kon kon", "কোন কোন", "কী কী", "name", "নাম", "list", "তালিকা", "gula", "গুলো"])
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
    Step 2: Run the LLM with already-retrieved context.
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
    return chain.invoke(messages)


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