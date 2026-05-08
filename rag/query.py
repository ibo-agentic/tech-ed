import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import chromadb
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()

# Lazy-load: model only loads on first use
_embedding_model = None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        print("Loading embedding model (first use)...")
        _embedding_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        print("Embedding model ready.")
    return _embedding_model

# ChromaDB still loads at startup (it's fast)
chroma = chromadb.PersistentClient(path="./chroma_db")
collection = chroma.get_or_create_collection(
    name="biology_nctb",
    metadata={"hnsw:space": "cosine"}
)


def get_relevant_chunks(student_question: str, top_k: int = 3) -> str:
    model = get_embedding_model()  # Loads here on first call
    q_embedding = model.encode(student_question).tolist()

    results = collection.query(
        query_embeddings=[q_embedding],
        n_results=top_k
    )

    chunks = results["documents"][0] if results["documents"] else []

    if not chunks:
        return "এই প্রশ্নের সম্পর্কিত তথ্য NCTB বইয়ে পাওয়া যায়নি।"

    return "\n\n---\n\n".join(chunks)