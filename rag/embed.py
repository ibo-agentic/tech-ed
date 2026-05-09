import os
import shutil
from pathlib import Path
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from dotenv import load_dotenv

load_dotenv()

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    separators=["\n\n", "\n", "।", ".", " "]
)

all_documents = []
ssc_dir = Path("books/ssc")

if not ssc_dir.exists():
    print("ERROR: books/ssc/ folder not found!")
    exit(1)

# Walk: books/ssc/{subject}/*.txt
for subject_folder in sorted(ssc_dir.iterdir()):
    if not subject_folder.is_dir():
        continue
    
    subject = subject_folder.name  # "biology", "physics", etc.
    print(f"\nProcessing {subject}...")
    
    # Find .txt files (use chunks/ if it exists)
    txt_files = list(subject_folder.glob("*.txt"))
    chunks_dir = subject_folder / "chunks"
    if chunks_dir.exists():
        txt_files = list(chunks_dir.glob("*.txt"))
        print(f"  Using chunks folder")
    
    if not txt_files:
        print(f"  No .txt files found")
        continue
    
    print(f"  Found {len(txt_files)} files")
    
    for txt_file in txt_files:
        try:
            loader = TextLoader(str(txt_file), encoding="utf-8")
            docs = loader.load()
            chunks = splitter.split_documents(docs)
            
            chapter_name = txt_file.stem
            for chunk in chunks:
                chunk.metadata.update({
                    "level": "ssc",
                    "subject": subject,
                    "chapter": chapter_name,
                    "source_file": str(txt_file)
                })
            
            all_documents.extend(chunks)
            print(f"     {chapter_name}: {len(chunks)} chunks")
        except Exception as e:
            print(f"     Error in {txt_file}: {e}")

print(f"\nTotal chunks: {len(all_documents)}")

if not all_documents:
    print("No documents to embed.")
    exit(1)

if os.path.exists("chroma_db"):
    shutil.rmtree("chroma_db")
    print("Old chroma_db deleted")

print("Embedding...")
vectorstore = Chroma.from_documents(
    documents=all_documents,
    embedding=embeddings,
    persist_directory="chroma_db"
)

subjects = sorted(set(d.metadata['subject'] for d in all_documents))
print(f"\nDone!")
print(f"Subjects: {subjects}")
print(f"Total chunks: {len(all_documents)}")