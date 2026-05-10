from dotenv import load_dotenv
load_dotenv()
import sys
sys.path.append('rag')
from query import vectorstore

r = vectorstore.get(where={"is_toc": True})
print(f"Found {len(r['ids'])} TOC chunks\n")

for m, d in zip(r['metadatas'], r['documents']):
    print('=' * 60)
    print(f"Subject: {m.get('subject')}")
    print(f"Chapter: {m.get('chapter')}")
    print('-' * 60)
    print(d)
    print()