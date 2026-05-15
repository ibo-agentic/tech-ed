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
from chain import flash_llm, gemini_pro_llm, gpt54_mini_llm

load_dotenv()

# Vision-capable parsers
parser = StrOutputParser()
flash_vision_chain = flash_llm | parser
pro_vision_chain = gemini_pro_llm | parser
gpt54_mini_vision_chain = gpt54_mini_llm | parser


# The frontend sends this when the user uploads an image without typing anything
_DEFAULT_CAPTION = "এই ছবিটি দেখে বুঝিয়ে দাও।"


def detect_subject_from_image(image_base64: str, image_type: str) -> str:
    """Use Flash to classify the image. Returns 'accounting_hard', 'accounting_simple', or subject name."""
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{image_base64}"}},
        {"type": "text", "text": (
            "One word only. Is this a complex multi-step accounting problem "
            "(ledger, trial balance, balance sheet, income statement with many calculations)? "
            "Reply: accounting_hard, accounting_simple, biology, geography, or other."
        )},
    ])
    result = flash_vision_chain.invoke([msg]).strip().lower()
    if "accounting_hard" in result:
        return "accounting_hard"
    if "accounting" in result:
        return "accounting_simple"
    return result


def pick_vision_chain(image_base64: str, image_type: str) -> tuple:
    """Detect subject from image and return (chain, detected_subject)."""
    subject = detect_subject_from_image(image_base64, image_type)
    if subject == "accounting_hard":
        print(f"🧮 [Image Routing] complex accounting → Gemini 2.5 Pro")
        return pro_vision_chain, "accounting"
    print(f"💬 [Image Routing] {subject} → Gemini 2.5 Flash")
    return flash_vision_chain, subject


def build_image_system_prompt(nctb_context: str, project_instructions: str = "") -> str:
    """Build system prompt for image queries with optional project instructions."""
    prompt = SYSTEM_PROMPT

    if project_instructions and project_instructions.strip():
        prompt += f"""

## এই প্রজেক্টের বিশেষ নির্দেশনা (Project Instructions):

{project_instructions.strip()}

উপরের নির্দেশনা সবসময় মেনে চলবে।

---
"""

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
):
    """
    Get AI answer for an image query.
    
    For accounting subject: also verifies math balances after Pro responds.
    If verification fails, retries once with explicit "fix the math" prompt.
    If still wrong, returns honest "couldn't verify" message.
    """
    # Auto-detect subject from image — overrides whatever the frontend sent
    selected_chain, subject = pick_vision_chain(image_base64, image_type)

    # Fetch RAG context using detected subject
    query_for_rag = user_input if user_input else "প্রশ্ন"
    nctb_context = get_relevant_chunks(query_for_rag, subject=subject)

    system_with_context = build_image_system_prompt(nctb_context, project_instructions)

    messages = [SystemMessage(content=system_with_context)]

    # Accounting images always get a clean context — no history bleed.
    # Text follow-ups ("step by step", "বুঝলাম না") go through chain.py
    # which has full history, so students can still ask follow-ups.
    if subject != "accounting":
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

    content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image_type};base64,{image_base64}"},
        }
    ]

    if user_input:
        content.append({"type": "text", "text": user_input})
    else:
        content.append({
            "type": "text",
            "text": (
                "ছবিতে যা আছে সেটা NCTB format-এ সম্পূর্ণ সমাধান করো। "
                "ধাপে ধাপে বাংলায় explain করো এবং proper bivoroni table format ব্যবহার করো। "
                "প্রতিটা subtotal verify করে দেখাও। "
                "শেষে balance sheet match করছে কিনা check করো।"
            ),
        })

    messages.append(HumanMessage(content=content))

    # ── First attempt ──
    answer = selected_chain.invoke(messages)
    
    # ── Verification (accounting only) ──
    if subject != "accounting":
        return answer
    
    is_valid, errors = verify_accounting_answer(answer)
    
    if is_valid:
        print(f"✅ [Verify] Accounting math checks out")
        return answer
    
    # ── First attempt failed verification — retry with explicit fix prompt ──
    print(f"⚠️ [Verify] Math errors detected: {errors}")
    print(f"🔄 [Verify] Asking Pro to recalculate...")
    
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
        print(f"✅ [Verify] Retry successful — math now balances")
        return retry_answer
    
    # ── Both attempts failed — honest fallback ──
    print(f"❌ [Verify] Both attempts failed: {retry_errors}")
    
    fallback = (
        f"আপু/ভাই, এই অঙ্কটা আমি দুইবার চেষ্টা করেছি কিন্তু math ঠিকমতো verify করতে পারছি না 🌱\n\n"
        f"যা পেয়েছি সেটা নিচে দিচ্ছি, কিন্তু **বইয়ের সমাধান বা স্যারের সাথে অবশ্যই check করো**:\n\n"
        f"---\n\n"
        f"{retry_answer}\n\n"
        f"---\n\n"
        f"⚠️ **সতর্কতা:** এই উত্তরে calculation error থাকতে পারে। "
        f"বইয়ের সমাধান দেখে নিজে verify করে নিও — exam-এ এই answer copy করো না যতক্ষণ না নিজে check করেছ।"
    )
    return fallback