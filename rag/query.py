import os
import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

chroma = chromadb.PersistentClient(path="./chroma_db")
collection = chroma.get_or_create_collection(
    name="biology_nctb",
    metadata={"hnsw:space": "cosine"}
)


def get_relevant_chunks(student_question: str, top_k: int = 3) -> str:
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=student_question
    )
    q_embedding = response.data[0].embedding

    results = collection.query(
        query_embeddings=[q_embedding],
        n_results=top_k
    )

    chunks = results["documents"][0] if results["documents"] else []

    if not chunks:
        return "এই প্রশ্নের সম্পর্কিত তথ্য NCTB বইয়ে পাওয়া যায়নি।"

    return "\n\n---\n\n".join(chunks)