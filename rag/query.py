import os
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from dotenv import load_dotenv

load_dotenv()

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

# Tuned for text-embedding-3-small + Bangla queries.
# Real subject questions score ~1.0-1.2, casual chat scores 1.4+.
RELEVANCE_THRESHOLD = 1.20


def _search_with_relevance(question, subject, top_k):
    if not subject:
        return []

    try:
        results = vectorstore.similarity_search_with_score(
            question, k=top_k, filter={"subject": subject}
        )
        relevant = [(doc, score) for doc, score in results if score <= RELEVANCE_THRESHOLD]

        all_scores = [round(s, 3) for _, s in results]
        kept_scores = [round(s, 3) for _, s in relevant]
        print(f"[RAG] q='{question[:50]}' subject={subject} all={all_scores} kept={kept_scores}")

        return relevant
    except Exception as e:
        print(f"[RAG ERROR] {e}")
        return []


def get_relevant_chunks(question, subject=None, top_k=3):
    """Retrieve textbook chunks. Returns empty string if nothing relevant."""
    relevant = _search_with_relevance(question, subject, top_k)
    if not relevant:
        return ""

    chunks = []
    for doc, _ in relevant:
        subj = doc.metadata.get("subject", "?")
        chapter = doc.metadata.get("chapter", "")  # Already clean from chapters.py

        label = f"[SSC {subj.capitalize()}"
        if chapter:
            label += f" — {chapter}"
        label += "]"

        chunks.append(f"{label}\n{doc.page_content}")

    return "\n\n---\n\n".join(chunks)


def get_chapters_for_question(question, subject="biology", top_k=3):
    """Returns clean chapter names directly from metadata. Empty list = not a subject question."""
    relevant = _search_with_relevance(question, subject, top_k)
    if not relevant:
        return []

    chapters = []
    seen = set()
    for doc, _ in relevant:
        chapter = doc.metadata.get("chapter", "")  # Already clean
        if chapter and chapter not in seen:
            chapters.append(chapter)
            seen.add(chapter)
    return chapters