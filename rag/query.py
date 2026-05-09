import os
import re
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from dotenv import load_dotenv

load_dotenv()

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embeddings)


# ── RELEVANCE THRESHOLD ──
# Chroma returns distances (lower = more similar). For text-embedding-3-small with
# cosine distance, real subject matches typically score 0.3–0.7. Casual chat scores
# 1.0+. We treat anything above this threshold as "not actually about biology".
#
# Tune this if you see false positives (casual chat triggers RAG) or false negatives
# (real biology questions skip RAG). 0.85 is a reasonable middle ground.
RELEVANCE_THRESHOLD = 0.85


# ── CHAPTER NAME MAPPING ──
BANGLA_ORDINAL_TO_CHAPTER = {
    "প্রথম":     "অধ্যায় ১: জীবন পাঠ",
    "দ্বিতীয়":   "অধ্যায় ২: জীবকোষ ও টিস্যু",
    "তৃতীয়":    "অধ্যায় ৩: কোষ বিভাজন",
    "চতুর্থ":    "অধ্যায় ৪: জীবনীশক্তি",
    "পঞ্চম":     "অধ্যায় ৫: খাদ্য, পুষ্টি ও পরিপাক",
    "ষষ্ঠ":      "অধ্যায় ৬: জীবে পরিবহন",
    "সপ্তম":    "অধ্যায় ৭: গ্যাসীয় বিনিময়",
    "অষ্টম":    "অধ্যায় ৮: রেচন প্রক্রিয়া",
    "নবম":      "অধ্যায় ৯: দৃঢ়তা প্রদান ও চলন",
    "দশম":      "অধ্যায় ১০: সমন্বয়",
    "একাদশ":   "অধ্যায় ১১: জীবের প্রজনন",
    "দ্বাদশ":    "অধ্যায় ১২: জীবের বংশগতি ও বিবর্তন",
    "ত্রয়োদশ":  "অধ্যায় ১৩: জীবের পরিবেশ",
    "চতুর্দশ":   "অধ্যায় ১৪: জীবপ্রযুক্তি",
}


def clean_chapter_name(raw):
    """Convert raw chunk ID like 'পঞ্চম_chunk_3' into a clean display name."""
    if not raw:
        return ""

    s = str(raw).strip()
    s = re.sub(r'_chunk_\d+$', '', s)
    s = re.sub(r'_\d+$', '', s)

    if s in BANGLA_ORDINAL_TO_CHAPTER:
        return BANGLA_ORDINAL_TO_CHAPTER[s]

    first_token = s.split('_')[0].split()[0] if s else ""
    if first_token in BANGLA_ORDINAL_TO_CHAPTER:
        return BANGLA_ORDINAL_TO_CHAPTER[first_token]

    return "জীববিজ্ঞান অধ্যায়"


def _search_with_relevance(question, subject, top_k):
    """
    Internal: returns only docs that pass the relevance threshold.
    Lower score = more similar in Chroma's distance metric.
    """
    if not subject:
        return []

    try:
        results = vectorstore.similarity_search_with_score(
            question,
            k=top_k,
            filter={"subject": subject}
        )
        # results = [(Document, score), ...]
        relevant = [(doc, score) for doc, score in results if score <= RELEVANCE_THRESHOLD]

        # Debug log — helpful while tuning the threshold
        all_scores = [round(s, 3) for _, s in results]
        kept_scores = [round(s, 3) for _, s in relevant]
        print(f"[RAG] q='{question[:50]}' all_scores={all_scores} kept={kept_scores}")

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
    for doc, score in relevant:
        subj = doc.metadata.get("subject", "?")
        chapter_clean = clean_chapter_name(doc.metadata.get("chapter", ""))

        label = f"[SSC {subj.capitalize()}"
        if chapter_clean:
            label += f" — {chapter_clean}"
        label += "]"

        chunks.append(f"{label}\n{doc.page_content}")

    return "\n\n---\n\n".join(chunks)


def get_chapters_for_question(question, subject="biology", top_k=3):
    """Returns clean chapter names. Empty list = not a subject question."""
    relevant = _search_with_relevance(question, subject, top_k)
    if not relevant:
        return []

    chapters = []
    seen = set()
    for doc, score in relevant:
        clean = clean_chapter_name(doc.metadata.get("chapter", ""))
        if clean and clean not in seen:
            chapters.append(clean)
            seen.add(clean)
    return chapters