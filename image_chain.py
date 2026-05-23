import os
import sys

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv
from verify import verify_accounting_answer
from prompt import SYSTEM_PROMPT

# RAG import
sys.path.append(os.path.join(os.path.dirname(__file__), "rag"))
from query import get_relevant_chunks

# Reuse the LLM clients from chain.py — single source of truth
from chain import flash_llm, vision_llm, gemini_pro_llm, gpt54_mini_llm

load_dotenv()

# Vision-capable parsers
parser = StrOutputParser()
flash_vision_chain = vision_llm | parser          # Gemini 2.5 Flash: vision-capable, classification + image solving
pro_vision_chain = gemini_pro_llm | parser
gpt54_mini_vision_chain = gpt54_mini_llm | parser


# The frontend sends this when the user uploads an image without typing anything
_DEFAULT_CAPTION = "এই ছবিটি দেখে বুঝিয়ে দাও।"


def detect_subject_from_image(image_base64: str, image_type: str) -> str:
    """Use Flash to classify the image. Returns 'accounting_hard', 'accounting_simple', or subject name."""
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{image_base64}"}},
        {"type": "text", "text": (
            "One word only. Classify this image:\n"
            "- accounting_hard: contains actual numbers to calculate "
            "(ledger, trial balance, income statement, cash flow with figures).\n"
            "- accounting_simple: accounting theory with NO numbers (definitions, discussion).\n"
            "- math: ANY of these — geometry theorems/proofs, algebra equations, statistics, "
            "trigonometry, coordinate geometry, number systems (natural/integer/rational/irrational/real), "
            "fractions P/Q, sets, functions, উপপাদ্য, জ্যামিতি, সমীকরণ, পরিসংখ্যান, "
            "সংখ্যা, মূলদ, অমূলদ, বাস্তব সংখ্যা, স্বাভাবিক সংখ্যা, পূর্ণসংখ্যা.\n"
            "- biology: cells, plants, animals, human body, ecosystems.\n"
            "- physics: motion, force, electricity, optics, waves.\n"
            "- chemistry: atoms, molecules, reactions, periodic table.\n"
            "- geography: maps, climate, rivers, population, landforms.\n"
            "Reply with exactly one word."
        )},
    ])
    result = flash_vision_chain.invoke([msg]).strip().lower()
    if "accounting_hard" in result:
        return "accounting_hard"
    if "accounting" in result:
        return "accounting_simple"
    if any(w in result for w in ("math", "geometry", "উপপাদ্য", "জ্যামিতি", "সংখ্যা", "number", "algebra", "trigon")):
        return "math"
    return result


def pick_vision_chain(image_base64: str, image_type: str) -> tuple:
    """Detect subject from image and return (chain, detected_subject)."""
    subject = detect_subject_from_image(image_base64, image_type)
    if subject == "accounting_hard":
        print(f"[Image Routing] complex accounting -> Gemini 2.5 Pro")
        return pro_vision_chain, "accounting"
    print(f"[Image Routing] {subject} -> Gemini 2.5 Flash")
    return flash_vision_chain, subject


def extract_problem_from_image(image_base64: str, image_type: str, user_caption: str = "") -> str:
    """Step 1: Gemini reads the image and returns a full text extraction of the problem."""
    prompt = (
        "ছবিতে যা আছে সম্পূর্ণ text-এ লেখো — সব প্রশ্ন (ক/খ/গ/ঘ সহ), "
        "সব দেওয়া তথ্য, সংখ্যা, diagram description। "
        "শুধু extract করো, কোনো solution দেবে না।"
    )
    if user_caption:
        prompt += f"\n\nছাত্রের নির্দেশ: {user_caption}"
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{image_base64}"}},
        {"type": "text", "text": prompt},
    ])
    return flash_vision_chain.invoke([msg]).strip()


def build_image_system_prompt(nctb_context: str, project_instructions: str = "", student_name: str = "", student_profile: dict = None) -> str:
    """Build system prompt for image queries — reuses chain.py builder so profile injection is shared."""
    from chain import build_system_prompt
    prompt = build_system_prompt("", project_instructions, student_name=student_name, student_profile=student_profile)

    if nctb_context and nctb_context.strip():
        prompt += f"""

## NCTB বই থেকে প্রাসঙ্গিক তথ্য:

{nctb_context}

---
ছবিতে যা আছে সেটা বিশ্লেষণ করো এবং উপরের NCTB তথ্য ব্যবহার করে উত্তর দাও।
NCTB-তে না থাকলে নিজের জ্ঞান থেকে NCTB style-এ উত্তর দাও।
"""

    return prompt


def get_answer_with_image(
    user_input,
    history,
    image_base64,
    image_type="image/jpeg",
    project_instructions: str = "",
    subject: str = "biology",
    stream: str = "",
    student_name: str = "",
    student_profile: dict = None,
):
    """
    Get AI answer for an image query.

    For accounting subject: also verifies math balances after Pro responds.
    If verification fails, retries once with explicit "fix the math" prompt.
    If still wrong, returns honest "couldn't verify" message.
    """
    from chain import check_stream_mismatch

    # Auto-detect subject from image — overrides whatever the frontend sent
    selected_chain, subject = pick_vision_chain(image_base64, image_type)

    # Stream mismatch check — return redirect before hitting the LLM
    mismatch = check_stream_mismatch(stream, subject)
    if mismatch:
        return mismatch, False, subject

    # Fetch RAG context using detected subject
    query_for_rag = user_input if user_input else "প্রশ্ন"
    nctb_context = get_relevant_chunks(query_for_rag, subject=subject)

    system_with_context = build_image_system_prompt(nctb_context, project_instructions, student_name=student_name, student_profile=student_profile)

    messages = [SystemMessage(content=[{
        "type": "text",
        "text": system_with_context,
        "cache_control": {"type": "ephemeral"},
    }])]

    # Accounting images always get a clean context — no history bleed.
    # Text follow-ups ("step by step", "বুঝলাম না") go through chain.py
    # which has full history, so students can still ask follow-ups.
    if subject != "accounting":
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

    # Non-accounting + user has a real question → solve directly with vision
    if subject != "accounting" and user_input and user_input != _DEFAULT_CAPTION:
        print("[Image Pipeline] Direct vision solve")
        messages.append(HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{image_base64}"}},
            {"type": "text", "text": user_input},
        ]))
        answer = selected_chain.invoke(messages)
        # If model still refuses due to image quality, force a solve retry
        _blur_signals = ["ঝাপসা", "অস্পষ্ট", "পড়তে পারছি না", "blurry", "unclear", "can't read"]
        if any(s in answer for s in _blur_signals):
            print("[Image Pipeline] Blur response detected — forcing solve retry")
            retry_msgs = messages[:-1] + [HumanMessage(content=[
                {"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{image_base64}"}},
                {"type": "text", "text": (
                    f"{user_input}\n\n"
                    "ছবি যতটুকু দেখা যাচ্ছে সেটা দিয়েই এখনই সম্পূর্ণ সমাধান দাও। "
                    "ছবির quality নিয়ে কিছু বলবে না — সরাসরি solve করো।"
                )},
            ])]
            answer = selected_chain.invoke(retry_msgs)
        return answer, True, subject

    content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image_type};base64,{image_base64}"},
        }
    ]

    if user_input and user_input != _DEFAULT_CAPTION:
        content.append({"type": "text", "text": user_input})
    elif subject == "accounting":
        # Accounting: always solve fully — math is too complex to guide step-by-step
        content.append({
            "type": "text",
            "text": (
                "ছবিতে যা আছে সেটা NCTB format-এ সম্পূর্ণ সমাধান করো। "
                "ধাপে ধাপে বাংলায় explain করো এবং proper bivoroni table format ব্যবহার করো। "
                "প্রতিটা subtotal verify করে দেখাও। "
                "শেষে balance sheet match করছে কিনা check করো।"
            ),
        })
    else:
        # Read the image and decide: single problem → solve directly; multi-part → list then ask
        content.append({
            "type": "text",
            "text": (
                "ছবিতে যা আছে মনোযোগ দিয়ে পড়ো।\n\n"
                "যদি ছবিতে শুধু একটাই প্রশ্ন বা সমস্যা থাকে (ক/খ/গ/ঘ ভাগ নেই): "
                "সরাসরি সম্পূর্ণ সমাধান করো। প্রশ্ন করার দরকার নেই।\n\n"
                "যদি ছবিতে সত্যিই একাধিক অংশ থাকে (ক. খ. গ. ঘ. লেখা আছে): "
                "প্রতিটি অংশ সংক্ষেপে তালিকা করো, তারপর জিজ্ঞেস করো "
                "'কোন অংশটা solve করব, নাকি সবগুলো একসাথে?'\n\n"
                "গুরুত্বপূর্ণ: ছবিতে ক/খ/গ/ঘ না থাকলে কখনো কাল্পনিক ক/খ/গ/ঘ তৈরি করবে না।"
            ),
        })

    messages.append(HumanMessage(content=content))

    # ── First attempt ──
    answer = selected_chain.invoke(messages)

    # ── Blur-response guard (non-accounting) ──
    if subject != "accounting":
        _blur_signals = ["ঝাপসা", "অস্পষ্ট", "পড়তে পারছি না", "blurry", "unclear", "can't read"]
        if any(s in answer for s in _blur_signals):
            print("[Image Pipeline] Blur response on default path — forcing solve retry")
            retry_content = [
                {"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{image_base64}"}},
                {"type": "text", "text": (
                    "ছবি যতটুকু দেখা যাচ্ছে সেটা দিয়েই এখনই পড়ো ও সমাধান দাও। "
                    "ছবির quality নিয়ে কিছু বলবে না — সরাসরি কাজ করো।"
                )},
            ]
            answer = selected_chain.invoke(messages[:-1] + [HumanMessage(content=retry_content)])
        chips = "image_question" if (not user_input or user_input == _DEFAULT_CAPTION) else True
        return answer, chips, subject

    is_valid, errors = verify_accounting_answer(answer)

    if is_valid:
        print(f"[Verify] Accounting math checks out")
        return answer, False, subject

    # ── First attempt failed verification — retry with explicit fix prompt ──
    print(f"[Verify] Math errors detected: {errors}")
    print(f"[Verify] Asking Pro to recalculate...")
    
    retry_message = HumanMessage(content=(
        f"তোমার আগের উত্তরে math এ ভুল আছে:\n\n"
        f"{chr(10).join('- ' + e for e in errors)}\n\n"
        f"এগুলো ঠিক করে আবার সম্পূর্ণ সমাধান লেখো। "
        f"বিশেষ করে check করো:\n"
        f"১. মোট প্রাপ্তি = মোট প্রদান (প্রাপ্তি প্রদান হিসাবে)\n"
        f"২. মোট সম্পদ = মোট দায় + মালিকানা স্বত্ব (আর্থিক অবস্থার বিবরণীতে)\n"
        f"৩. প্রতিটা যোগফল ধাপে ধাপে verify করো।\n\n"
        f"সব হিসাব ঠিকভাবে balance হলে তবেই উত্তর দাও।"
    ))
    
    retry_messages = messages + [AIMessage(content=answer), retry_message]
    retry_answer = selected_chain.invoke(retry_messages)
    
    # ── Verify again ──
    is_valid_retry, retry_errors = verify_accounting_answer(retry_answer)
    
    if is_valid_retry:
        print(f"[Verify] Retry successful — math now balances")
        return retry_answer, False, subject

    # ── Both attempts failed — honest fallback ──
    print(f"[Verify] Both attempts failed: {retry_errors}")
    
    fallback = (
        f"আপু/ভাই, এই অঙ্কটা আমি দুইবার চেষ্টা করেছি কিন্তু math ঠিকমতো verify করতে পারছি না 🌱\n\n"
        f"যা পেয়েছি সেটা নিচে দিচ্ছি, কিন্তু **বইয়ের সমাধান বা স্যারের সাথে অবশ্যই check করো**:\n\n"
        f"---\n\n"
        f"{retry_answer}\n\n"
        f"---\n\n"
        f"⚠️ **সতর্কতা:** এই উত্তরে calculation error থাকতে পারে। "
        f"বইয়ের সমাধান দেখে নিজে verify করে নিও — exam-এ এই answer copy করো না যতক্ষণ না নিজে check করেছ।"
    )
    return fallback, False, subject