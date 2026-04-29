"""
embed.py — Run this ONCE after extract.py finishes.
Reads all chunks from biology_chapters/chunks/
Embeds them with OpenAI and stores in ChromaDB.
"""

import os
import glob
import chromadb
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ChromaDB stored locally in your project folder
chroma = chromadb.PersistentClient(path="./chroma_db")
collection = chroma.get_or_create_collection(
    name="biology_nctb",
    metadata={"hnsw:space": "cosine"}
)

def get_embedding(text: str) -> list:
    response = client.embeddings.create(
        input=text,
        model="text-embedding-3-small"  # cheapest, works great for Bangla
    )
    return response.data[0].embedding

def index_all_chunks():
    chunk_files = glob.glob("biology_chapters/chunks/*.txt")

    if not chunk_files:
        print("ERROR: No chunk files found.")
        print("Make sure you ran extract.py first and biology_chapters/chunks/ exists.")
        return

    print(f"Found {len(chunk_files)} chunks to embed...")
    print("This runs once — takes ~2-3 minutes and costs < $0.01\n")

    for i, filepath in enumerate(chunk_files):
        chunk_id = os.path.basename(filepath).replace(".txt", "")

        # Skip if already indexed (safe to re-run)
        existing = collection.get(ids=[chunk_id])
        if existing["ids"]:
            print(f"  [{i+1}/{len(chunk_files)}] Already indexed: {chunk_id}")
            continue

        with open(filepath, encoding="utf-8") as f:
            text = f.read().strip()

        if not text:
            continue

        embedding = get_embedding(text)

        collection.add(
            documents=[text],
            embeddings=[embedding],
            ids=[chunk_id],
            metadatas=[{"source": filepath}]
        )
        print(f"  [{i+1}/{len(chunk_files)}] Indexed: {chunk_id}")

    total = collection.count()
    print(f"\nDone! Total chunks in ChromaDB: {total}")
    print("Your biology_nctb collection is ready.")

if __name__ == "__main__":
    index_all_chunks()