"""
Add new subject content to existing ChromaDB.

Default mode: APPEND — adds new chunks without touching existing data.
Use --rebuild flag only when you need to re-ingest everything from scratch
(e.g., after fixing chunk metadata for an existing subject).
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent))
from chapters import parse_chunk_filename, CHAPTERS, SUBJECT_STREAM

load_dotenv()

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    separators=["\n\n", "\n", "।", ".", " "],
)


def create_toc_chunk(subject):
    """Create a special meta-chunk listing all chapters for a subject."""
    if subject not in CHAPTERS:
        return None
    
    chapter_list = "\n".join([
        f"অধ্যায় {num}: {title}"
        for ordinal, (num, title) in CHAPTERS[subject].items()
    ])
    
    # Subject names in multiple forms so retrieval catches Banglish queries
    subject_names = {
        "biology": "জীববিজ্ঞান biology bio",
        "geography": "ভূগোল bhugol bugol geography ভূগোল ও পরিবেশ",
    }
    aliases = subject_names.get(subject, subject)

    content = f"""SSC NCTB {aliases} বইয়ের অধ্যায় তালিকা।
Table of Contents — chapter list — chapter name — অধ্যায়ের নাম — chapter gula — chapters of {subject}.

বইয়ে মোট {len(CHAPTERS[subject])}টি অধ্যায় আছে:

{chapter_list}

অধ্যায় কয়টি? কয়টি chapter? কোন কোন অধ্যায় আছে? Chapter list ki ki? Chapter er name bolo. Chapter gula ki ki? কী কী অধ্যায় পড়ানো হয়?"""
    
    return Document(
        page_content=content,
        metadata={
            "subject": subject,
            "stream": SUBJECT_STREAM.get(subject, "unknown"),
            "chapter_number": 0,
            "chapter": "অধ্যায় তালিকা (Table of Contents)",
            "chapter_title": "Table of Contents",
            "chunk_id": f"{subject}_toc",
            "level": "ssc",
            "is_toc": True,
        }
    )


def collect_documents(ssc_dir: Path, subjects_filter=None):
    """
    Walk books/ssc/{subject}/chunks/*.txt.

    If subjects_filter is provided (list of subject names), only process
    those subfolders. Otherwise process all subjects found.
    """
    all_docs = []
    skipped = []

    for subject_folder in sorted(ssc_dir.iterdir()):
        if not subject_folder.is_dir():
            continue

        subject = subject_folder.name

        # Skip subjects not in the filter (when filter is set)
        if subjects_filter and subject not in subjects_filter:
            print(f"\nSkipping {subject} (not in filter)")
            continue

        print(f"\nProcessing {subject}...")

        chunks_dir = subject_folder / "chunks"
        if chunks_dir.exists():
            txt_files = sorted(chunks_dir.glob("*.txt"))
            print(f"  Using chunks/ ({len(txt_files)} files)")
        else:
            txt_files = sorted(subject_folder.glob("*.txt"))
            print(f"  Using top-level ({len(txt_files)} files)")

        if not txt_files:
            print(f"  No files, skipping")
            continue

        for txt_file in txt_files:
            metadata = parse_chunk_filename(txt_file.name, subject)
            if metadata is None:
                skipped.append(str(txt_file))
                continue

            try:
                loader = TextLoader(str(txt_file), encoding="utf-8")
                docs = loader.load()
                chunks = splitter.split_documents(docs)

                for chunk in chunks:
                    chunk.metadata.update(metadata)
                    chunk.metadata["level"] = "ssc"
                    chunk.metadata["source_file"] = str(txt_file)

                all_docs.extend(chunks)
                print(f"   {metadata['chapter']}: {len(chunks)} chunks")
            except Exception as e:
                print(f"   Error in {txt_file}: {e}")

    if skipped:
        print(f"\n⚠️  Skipped {len(skipped)} unrecognized files:")
        for f in skipped[:5]:
            print(f"   - {f}")
        if len(skipped) > 5:
            print(f"   ... and {len(skipped) - 5} more")
        print("  → Add these chapters to chapters.py registry, then re-run.")

    return all_docs


def main():
    parser = argparse.ArgumentParser(description="Embed SSC textbook content into ChromaDB.")
    parser.add_argument(
        "--subjects",
        nargs="+",
        help="Only process these subjects (e.g. --subjects geography). "
             "Default: process only NEW subjects not yet in chroma_db.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="DESTRUCTIVE: delete chroma_db and re-embed everything from scratch.",
    )
    args = parser.parse_args()

    ssc_dir = Path("books/ssc")
    if not ssc_dir.exists():
        print("ERROR: books/ssc/ not found")
        return

    db_exists = os.path.exists("chroma_db")

    # ── REBUILD MODE ──
    if args.rebuild:
        if db_exists:
            ans = input("\n⚠️  --rebuild will DELETE chroma_db and re-embed EVERYTHING.\nType 'yes' to confirm: ").strip().lower()
            if ans != "yes":
                print("Aborted.")
                return
            shutil.rmtree("chroma_db")
            print("Old chroma_db deleted.\n")

        all_docs = collect_documents(ssc_dir, subjects_filter=args.subjects)
        if not all_docs:
            print("No documents to embed.")
            return

        # Add TOC chunks for each subject
        for subject in CHAPTERS:
            toc = create_toc_chunk(subject)
            if toc:
                all_docs.append(toc)
                print(f"  Added TOC chunk for {subject}")

        print(f"\nEmbedding {len(all_docs)} chunks (including TOC)...")
        Chroma.from_documents(
            documents=all_docs,
            embedding=embeddings,
            persist_directory="chroma_db",
        )
        _print_summary(all_docs)
        return

    # ── APPEND MODE (default) ──
    if not db_exists:
        print("No existing chroma_db. Creating fresh.")
        all_docs = collect_documents(ssc_dir, subjects_filter=args.subjects)
        if not all_docs:
            return

        # Add TOC chunks for each subject
        for subject in CHAPTERS:
            toc = create_toc_chunk(subject)
            if toc:
                all_docs.append(toc)
                print(f"  Added TOC chunk for {subject}")

        print(f"\nEmbedding {len(all_docs)} chunks (including TOC)...")
        Chroma.from_documents(
            documents=all_docs,
            embedding=embeddings,
            persist_directory="chroma_db",
        )
        _print_summary(all_docs)
        return

    # DB exists — figure out which subjects are already embedded
    print("Existing chroma_db found. Checking which subjects are already embedded...")
    vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embeddings)
    existing = vectorstore.get(include=["metadatas"])
    existing_subjects = set()
    for m in (existing.get("metadatas") or []):
        if m and m.get("subject"):
            existing_subjects.add(m["subject"])
    print(f"  Already in DB: {sorted(existing_subjects) or '(none)'}")

    # If user specified subjects, use those. Otherwise auto-detect new subjects.
    if args.subjects:
        target_subjects = args.subjects
        # Warn if any target is already embedded
        overlap = set(target_subjects) & existing_subjects
        if overlap:
            print(f"\n⚠️  These subjects are already in chroma_db: {sorted(overlap)}")
            print("   Adding will create DUPLICATE chunks.")
            ans = input("   Type 'yes' to add anyway, or 'no' to skip them: ").strip().lower()
            if ans != "yes":
                target_subjects = [s for s in target_subjects if s not in existing_subjects]
                if not target_subjects:
                    print("Nothing to do.")
                    return
                print(f"   Will only add: {target_subjects}")
    else:
        # Auto-detect: anything in books/ssc/ that isn't in DB yet
        all_subject_folders = {p.name for p in ssc_dir.iterdir() if p.is_dir()}
        target_subjects = sorted(all_subject_folders - existing_subjects)
        if not target_subjects:
            print("\n✓ All subjects already embedded. Nothing to do.")
            print("  Use --rebuild to re-embed from scratch.")
            return
        print(f"  New subjects to add: {target_subjects}")

    new_docs = collect_documents(ssc_dir, subjects_filter=target_subjects)
    if not new_docs:
        print("\nNo new documents found.")
        return

    # Add TOC chunks for target subjects
    for subject in target_subjects:
        toc = create_toc_chunk(subject)
        if toc:
            new_docs.append(toc)
            print(f"  Added TOC chunk for {subject}")

    print(f"\nAdding {len(new_docs)} chunks to existing chroma_db (including TOC)...")
    vectorstore.add_documents(new_docs)
    _print_summary(new_docs, mode="added")


def _print_summary(docs, mode="embedded"):
    subjects = sorted(set(d.metadata["subject"] for d in docs))
    streams = sorted(set(d.metadata["stream"] for d in docs))
    print(f"\n✓ Done!  {mode.capitalize()} {len(docs)} chunks.")
    print(f"  Subjects: {subjects}")
    print(f"  Streams: {streams}")


if __name__ == "__main__":
    main()