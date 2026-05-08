from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv
from prompt import SYSTEM_PROMPT
import os
import sys

# RAG import
sys.path.append(os.path.join(os.path.dirname(__file__), "rag"))
from query import get_relevant_chunks

load_dotenv()

vision_llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0.7
)
parser = StrOutputParser()
vision_chain = vision_llm | parser


def build_image_system_prompt(nctb_context: str, project_instructions: str = "") -> str:
    """Build system prompt for image queries with optional project instructions."""
    
    prompt = SYSTEM_PROMPT
    
    # Project instructions first (highest priority)
    if project_instructions and project_instructions.strip():
        prompt += f"""

## এই প্রজেক্টের বিশেষ নির্দেশনা (Project Instructions):

{project_instructions.strip()}

উপরের নির্দেশনা সবসময় মেনে চলবে।

---
"""
    
    # Then NCTB context
    prompt += f"""

## NCTB বই থেকে প্রাসঙ্গিক তথ্য:
{nctb_context}

---
ছবিতে যা আছে সেটা বিশ্লেষণ করো এবং উপরের NCTB তথ্য ব্যবহার করে উত্তর দাও।
NCTB-তে না থাকলে নিজের জ্ঞান থেকে NCTB style-এ উত্তর দাও।
"""
    
    return prompt


def get_answer_with_image(user_input, history, image_base64, image_type="image/jpeg", project_instructions: str = ""):
    """
    Get AI answer for an image query with optional project instructions.
    
    Args:
        user_input: Optional text query alongside the image
        history: Last 10 messages
        image_base64: The image as base64 string
        image_type: MIME type (default: image/jpeg)
        project_instructions: Optional custom instructions from a Project
    """
    # Fetch RAG context
    query_for_rag = user_input if user_input else "জীববিজ্ঞান প্রশ্ন"
    nctb_context = get_relevant_chunks(query_for_rag)
    
    # Build system prompt with project instructions
    system_with_context = build_image_system_prompt(nctb_context, project_instructions)
    
    messages = [SystemMessage(content=system_with_context)]
    
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    
    content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image_type};base64,{image_base64}"}
        }
    ]
    
    if user_input:
        content.append({"type": "text", "text": user_input})
    else:
        content.append({
            "type": "text",
            "text": "ছবিতে যে প্রশ্ন আছে সেটা ধাপে ধাপে বাংলায় সমাধান করো এবং বোঝাও।"
        })
    
    messages.append(HumanMessage(content=content))
    return vision_chain.invoke(messages)