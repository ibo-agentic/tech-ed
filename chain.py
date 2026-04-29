from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv
from prompt import SYSTEM_PROMPT
import os
import sys

# Add rag/ folder to path so we can import query.py
sys.path.append(os.path.join(os.path.dirname(__file__), "rag"))
from query import get_relevant_chunks

load_dotenv()

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7
)
parser = StrOutputParser()
chain = llm | parser


def get_answer(user_input, history):
    # 1. Fetch relevant NCTB Biology chunks for this question
    nctb_context = get_relevant_chunks(user_input)

    # 2. Inject context into system prompt
    system_with_context = SYSTEM_PROMPT + f"""

## NCTB জীববিজ্ঞান বই থেকে প্রাসঙ্গিক তথ্য:

{nctb_context}

---
শুধুমাত্র উপরের তথ্য ব্যবহার করে উত্তর দাও।
যদি উত্তর context-এ না থাকে, বলো: "এই প্রশ্নের তথ্য বইয়ে পাওয়া যায়নি।"
"""

    # 3. Build messages with enriched system prompt
    messages = [SystemMessage(content=system_with_context)]

    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))

    messages.append(HumanMessage(content=user_input))
    return chain.invoke(messages)


if __name__ == "__main__":
    history = []

    while True:
        user_input = input("You: ")
        if user_input == "quit":
            break
        answer = get_answer(user_input, history)
        print(f"Dipti: {answer}\n")
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": answer})