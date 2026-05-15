"""
Extract NCTB SSC Physics (পদার্থবিজ্ঞান) chapters via OCR.

Verified against actual TOC (13 chapters). Book page X = PDF page X+5.
"""
import os
import re
import sys
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path

sys.path.append(str(Path(__file__).parent))
from chapters import CHAPTERS

# ── Windows paths ──
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\poppler\Library\bin"

PDF_PATH = r"Secondary (BV)-2026_Class 9-10_Physics_compressed.pdf"
OUTPUT_DIR = "books/ssc/physics"
DPI = 200

# PDF page = book page + 5  (5 pages of front matter before chapter 1)
PHYSICS_PAGES = {
    "প্রথম":     (6,   36),   # Book ১–৩১
    "দ্বিতীয়":   (37,  66),   # Book ৩২–৬১
    "তৃতীয়":    (67,  102),  # Book ৬২–৯৭
    "চতুর্থ":    (103, 131),  # Book ৯৮–১২৬
    "পঞ্চম":     (132, 163),  # Book ১২৭–১৫৮
    "ষষ্ঠ":      (164, 190),  # Book ১৫৯–১৮৫
    "সপ্তম":    (191, 214),  # Book ১৮৬–২০৯
    "অষ্টম":    (215, 245),  # Book ২১০–২৪০
    "নবম":      (246, 274),  # Book ২৪১–২৬৯
    "দশম":      (275, 302),  # Book ২৭০–২৯৭
    "একাদশ":   (303, 333),  # Book ২৯৮–৩২৮
    "দ্বাদশ":    (334, 350),  # Book ৩২৯–৩৪৫
    "ত্রয়োদশ":  (351, 385),  # Book ৩৪৬–end (adjust if PDF is shorter)
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
        labeled = f"[পদার্থবিজ্ঞান | {chapter_label}]\n\n{chunk}"
        chunks.append(labeled)
    return chunks


def extract_chapter(ordinal_key, start_page, end_page):
    chapter_number, chapter_title = CHAPTERS["physics"][ordinal_key]
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
    print("NCTB Physics (পদার্থবিজ্ঞান) OCR Extraction")
    print("=" * 60)

    if not os.path.exists(PDF_PATH):
        print(f"\nERROR: PDF not found at: {PDF_PATH}")
        print("Place the PDF in your project root.")
        return

    total = 0
    for ordinal_key, (start, end) in PHYSICS_PAGES.items():
        if ordinal_key not in CHAPTERS["physics"]:
            print(f"⚠️  Skipping {ordinal_key} — not in chapters.py")
            continue
        total += extract_chapter(ordinal_key, start, end)

    print(f"\n{'=' * 60}")
    print(f"DONE! Total chunks: {total}")
    print(f"Chunks saved in: {OUTPUT_DIR}/chunks/")
    print("\nNext steps:")
    print("  1. python rag/embed.py --subjects physics")
    print("  2. Test with: python check_toc.py")


if __name__ == "__main__":
    main()
