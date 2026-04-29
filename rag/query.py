"""
query.py — Called at runtime for every student message.
Retrieves the 3 most relevant NCTB chunks and returns them
as a string to inject into your system prompt.
"""

import os
import chromadb
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Reuse the same persistent DB
chroma = chromadb.PersistentClient(path="./chroma_db")
collection = chroma.get_or_create_collection(
    name="biology_nctb",
    metadata={"hnsw:space": "cosine"}
)

def get_relevant_chunks(student_question: str, top_k: int = 3) -> str:
    """
    Takes a student question, finds the top_k most relevant
    NCTB Biology chunks, returns them as a single string
    ready to inject into the system prompt.
    """
    # Embed the student's question
    response = client.embeddings.create(
        input=student_question,
        model="text-embedding-3-small"
    )
    q_embedding = response.data[0].embedding

    # Search ChromaDB
    results = collection.query(
        query_embeddings=[q_embedding],
        n_results=top_k
    )

    chunks = results["documents"][0]  # list of top_k matching texts

    if not chunks:
        return "এই প্রশ্নের সম্পর্কিত তথ্য NCTB বইয়ে পাওয়া যায়নি।"

    return "\n\n---\n\n".join(chunks)


def build_system_prompt(student_question: str, base_prompt: str) -> str:
    """
    Fetches relevant chunks and injects into system prompt.
    Call this before every GPT-4o mini API call.
    """
    context = get_relevant_chunks(student_question)

    return base_prompt + f"""

## NCTB জীববিজ্ঞান বই থেকে প্রাসঙ্গিক তথ্য:

{context}

---
শুধুমাত্র উপরের context ব্যবহার করে উত্তর দাও।
Context-এ না থাকলে বলো: "এই প্রশ্নের তথ্য বইয়ে পাওয়া যায়নি।"
"""


# Quick test
if __name__ == "__main__":
    test_q = "সালোকসংশ্লেষণ কী?"
    print("Question:", test_q)
    print("\nRetrieved context:")
    print(get_relevant_chunks(test_q))