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

load_dotenv()

llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.7
)
parser = StrOutputParser()
chain = llm | parser


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
        prompt += f"""

## NCTB জীববিজ্ঞান বই থেকে প্রাসঙ্গিক তথ্য:

{nctb_context}

---
উপরের তথ্য ব্যবহার করে উত্তর দাও।
"""

    return prompt


def do_rag_lookup(user_input: str):
    """
    Step 1: Run RAG retrieval ONLY. Returns (nctb_context, chapters_found).
    Separated so we can show progress to the user before the LLM call starts.
    """
    nctb_context = get_relevant_chunks(user_input, subject="biology")
    chapters_found = get_chapters_for_question(user_input, subject="biology")
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


def get_answer(user_input, history, project_instructions: str = ""):
    """
    Non-streaming version (kept for backwards compat with /ask-image and tests).
    """
    t0 = time.time()
    nctb_context, chapters_found = do_rag_lookup(user_input)

    print(f"\n📚 [DEBUG] User asked: {user_input}")
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