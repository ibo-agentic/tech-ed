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
from query import get_relevant_chunks

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
    
    # Inject project's custom instructions FIRST (highest priority)
    if project_instructions and project_instructions.strip():
        prompt += f"""

## এই প্রজেক্টের বিশেষ নির্দেশনা (Project Instructions):

{project_instructions.strip()}

উপরের নির্দেশনা সবসময় মেনে চলবে।

---
"""
    
    # Then NCTB context
    prompt += f"""

## NCTB জীববিজ্ঞান বই থেকে প্রাসঙ্গিক তথ্য:

{nctb_context}

---
শুধুমাত্র উপরের তথ্য ব্যবহার করে উত্তর দাও।
যদি উত্তর context-এ না থাকে, বলো: "এই প্রশ্নের তথ্য বইয়ে পাওয়া যায়নি।"
"""
    
    return prompt


def get_answer(user_input, history, project_instructions: str = ""):
    """
    Get AI answer with optional project-specific instructions.
    
    Args:
        user_input: Student's question
        history: Last 10 messages [{"role": "user/assistant", "content": "..."}]
        project_instructions: Optional custom instructions from a Project (default: "")
    """
    t0 = time.time()
    
    # 1. Fetch relevant NCTB Biology chunks
    nctb_context = get_relevant_chunks(user_input, subject="biology")

    t1 = time.time()
    
    # 2. Build system prompt (with project instructions if provided)
    system_with_context = build_system_prompt(nctb_context, project_instructions)
    
    # 3. Build messages
    messages = [SystemMessage(content=system_with_context)]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_input))
    t2 = time.time()
    
    # 4. Call LLM
    result = chain.invoke(messages)
    t3 = time.time()
    
    print(f"⏱️  RAG: {t1-t0:.2f}s | Build: {t2-t1:.2f}s | LLM: {t3-t2:.2f}s | TOTAL: {t3-t0:.2f}s")
    
    return result


def get_answer_stream(user_input, history, project_instructions: str = ""):
    """Streaming version with project instructions support."""
    nctb_context = get_relevant_chunks(user_input)
    system_with_context = build_system_prompt(nctb_context, project_instructions)
    
    messages = [SystemMessage(content=system_with_context)]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_input))
    
    for chunk in chain.stream(messages):
        yield chunk


# Quick test mode
if __name__ == "__main__":
    history = []
    print("Test mode — type 'quit' to exit\n")
    while True:
        user_input = input("You: ")
        if user_input.lower() == "quit":
            break
        answer = get_answer(user_input, history)
        print(f"Dipti: {answer}\n")
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": answer})