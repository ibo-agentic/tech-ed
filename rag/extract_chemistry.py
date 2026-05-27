"""
Extract NCTB SSC রসায়ন (Chemistry) chapters via OCR.

Verified TOC (12 chapters). Assumes book page + 5 = PDF page.
PDF page 6 = book page 1 = start of Chapter 1 (confirmed by OCR).
"""
import io
import os
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pytesseract
from pdf2image import convert_from_path

sys.path.append(str(Path(__file__).parent))
from chapters import CHAPTERS

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\poppler\Library\bin"

PDF_PATH = r"Secondary (BV)-2026_Class 9-10_Chemistry_compressed.pdf"
OUTPUT_DIR = "books/ssc/chemistry"
DPI = 200

# PDF page = book page + 5  (5 pages of front matter before page 1)
CHEMISTRY_PAGES = {
    "প্রথম":     (6,   21),   # Book 1–16    রসায়নের ধারণা
    "দ্বিতীয়":   (22,  39),   # Book 17–34   পদার্থের অবস্থা
    "তৃতীয়":    (40,  63),   # Book 35–58   পদার্থের গঠন
    "চতুর্থ":    (64,  86),   # Book 59–81   পর্যায় সারণি
    "পঞ্চম":     (87,  113),  # Book 82–108  রাসায়নিক বন্ধন
    "ষষ্ঠ":      (114, 146),  # Book 109–141 মোলের ধারণা ও রাসায়নিক গণনা
    "সপ্তম":    (147, 172),  # Book 142–167 রাসায়নিক বিক্রিয়া
    "অষ্টম":    (173, 210),  # Book 168–205 রসায়ন ও শক্তি
    "নবম":      (211, 237),  # Book 206–232 এসিড-ক্ষারক সমতা
    "দশম":      (238, 265),  # Book 233–260 খনিজ সম্পদ: ধাতু-অধাতু
    "একাদশ":   (266, 291),  # Book 261–286 খনিজ সম্পদ: জীবাশ্ম
    "দ্বাদশ":    (292, 310),  # Book 287–305 আমাদের জীবনে রসায়ন
}


def ocr_page(pil_image):
    return pytesseract.image_to_string(pil_image, lang="ben+eng")


def clean_text(text):
    text = re.sub(r"^\s*[০-৯\d]+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^\s*২০২৬\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*২০২৫\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


def chunk_text(text, chapter_label, max_words=400):
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunk = " ".join(words[i : i + max_words])
        labeled = f"[রসায়ন | {chapter_label}]\n\n{chunk}"
        chunks.append(labeled)
    return chunks


def extract_chapter(ordinal_key, start_page, end_page):
    chapter_number, chapter_title = CHAPTERS["chemistry"][ordinal_key]
    print(f"\n→ অধ্যায় {chapter_number}: {chapter_title}  (PDF pages {start_page}–{end_page})")

    all_text = f"অধ্যায় {chapter_number}: {chapter_title}\n\n"

    pages = convert_from_path(
        PDF_PATH,
        dpi=DPI,
        first_page=start_page,
        last_page=end_page,
        poppler_path=POPPLER_PATH,
    )

    for i, page_img in enumerate(pages):
        page_num = start_page + i
        print(f"   page {page_num}...", end="", flush=True)
        raw = ocr_page(page_img)
        all_text += clean_text(raw) + "\n\n"
        print(" ✓")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    chapter_file = os.path.join(OUTPUT_DIR, f"{ordinal_key}_{chapter_title[:25]}.txt")
    with open(chapter_file, "w", encoding="utf-8") as f:
        f.write(all_text)

    chunks_dir = os.path.join(OUTPUT_DIR, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)
    chunks = chunk_text(all_text, f"অধ্যায় {chapter_number} - {chapter_title}")
    for j, chunk in enumerate(chunks, start=1):
        out_path = os.path.join(chunks_dir, f"{ordinal_key}_chunk_{j}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(chunk)

    print(f"   Saved {len(chunks)} chunks")
    return len(chunks)


def main():
    print("=" * 60)
    print("NCTB রসায়ন (Chemistry) OCR Extraction")
    print("=" * 60)

    if not os.path.exists(PDF_PATH):
        print(f"\nERROR: PDF not found at: {PDF_PATH}")
        print("Set PDF_PATH at the top of this file to your actual filename.")
        return

    total = 0
    for ordinal_key, (start, end) in CHEMISTRY_PAGES.items():
        if ordinal_key not in CHAPTERS["chemistry"]:
            print(f"⚠️  Skipping {ordinal_key} — not in chapters.py")
            continue
        total += extract_chapter(ordinal_key, start, end)

    print(f"\n{'=' * 60}")
    print(f"DONE! Total chunks: {total}")
    print(f"Chunks saved in: {OUTPUT_DIR}/chunks/")
    print("\nNext steps:")
    print("  1. python rag/embed.py --subjects chemistry")
    print("  2. Restart app.py")


if __name__ == "__main__":
    main()
