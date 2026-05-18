"""
Extract NCTB SSC Math (গণিত) chapters via OCR.

Verified against actual TOC (17 chapters). Book page X = PDF page X+5.
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

PDF_PATH = r"Secondary (BV)-2026_Class 9-10_Math_compressed (1).pdf"
OUTPUT_DIR = "books/ssc/math"
DPI = 200

# PDF page = book page + 5
MATH_PAGES = {
    "প্রথম":     (6,   25),   # Book ১–২০
    "দ্বিতীয়":   (26,  47),   # Book ২১–৪২
    "তৃতীয়":    (48,  79),   # Book ৪৩–৭৪
    "চতুর্থ":    (80,  97),   # Book ৭৫–৯২
    "পঞ্চম":     (98,  115),  # Book ৯৩–১১০
    "ষষ্ঠ":      (116, 140),  # Book ১১১–১৩৫
    "সপ্তম":    (141, 156),  # Book ১৩৬–১৫১
    "অষ্টম":    (157, 178),  # Book ১৫২–১৭৩
    "নবম":      (179, 201),  # Book ১৭৪–১৯৬
    "দশম":      (202, 209),  # Book ১৯৭–২০৪
    "একাদশ":   (210, 228),  # Book ২০৫–২২৩
    "দ্বাদশ":    (229, 253),  # Book ২২৪–২৪৮
    "ত্রয়োদশ":  (254, 270),  # Book ২৪৯–২৬৫
    "চতুর্দশ":   (271, 289),  # Book ২৬৬–২৮৪
    "পঞ্চদশ":   (290, 298),  # Book ২৮৫–২৯৩
    "ষোড়শ":    (299, 330),  # Book ২৯৪–৩২৫
    "সপ্তদশ":   (331, 349),  # Book ৩২৬–৩৪৪ (PDF 362 total, front matter = 5 pages)
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
        labeled = f"[গণিত | {chapter_label}]\n\n{chunk}"
        chunks.append(labeled)
    return chunks


def extract_chapter(ordinal_key, start_page, end_page):
    chapter_number, chapter_title = CHAPTERS["math"][ordinal_key]
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
    print("NCTB Math (গণিত) OCR Extraction")
    print("=" * 60)

    if not os.path.exists(PDF_PATH):
        print(f"\nERROR: PDF not found at: {PDF_PATH}")
        return

    total = 0
    for ordinal_key, (start, end) in MATH_PAGES.items():
        if ordinal_key not in CHAPTERS["math"]:
            print(f"⚠️  Skipping {ordinal_key} — not in chapters.py")
            continue
        total += extract_chapter(ordinal_key, start, end)

    print(f"\n{'=' * 60}")
    print(f"DONE! Total chunks: {total}")
    print(f"Chunks saved in: {OUTPUT_DIR}/chunks/")
    print("\nNext steps:")
    print("  1. python rag/embed.py --subjects math")
    print("  2. Restart app.py")


if __name__ == "__main__":
    main()
