"""
Extract NCTB SSC Geography (ভূগোল ও পরিবেশ) chapters via OCR.

Verified against actual TOC. Book page X = PDF page X+5 (5-page front matter).
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

# Update this to wherever you save the PDF
PDF_PATH = r"Secondary (BV)-2026_Class 9-10_Bhugol_compressed.pdf"
OUTPUT_DIR = "books/ssc/geography"
DPI = 200

# Verified page ranges — PDF page numbers (book page + 5 offset)
# Book TOC says Ch1: pages 1-7, but PDF starts Ch1 at page 6, so PDF range is 6-12
GEOGRAPHY_PAGES = {
    "প্রথম":     (6,   12),   # Book ১-৭
    "দ্বিতীয়":   (13,  35),   # Book ৮-৩০
    "তৃতীয়":    (36,  51),   # Book ৩১-৪৬
    "চতুর্থ":    (52,  73),   # Book ৪৭-৬৮
    "পঞ্চম":     (74,  95),   # Book ৬৯-৯০
    "ষষ্ঠ":      (96,  109),  # Book ৯১-১০৪
    "সপ্তম":    (110, 130),  # Book ১০৫-১২৫
    "অষ্টম":    (131, 142),  # Book ১২৬-১৩৭
    "নবম":      (143, 151),  # Book ১৩৮-১৪৬
    "দশম":      (152, 168),  # Book ১৪৭-১৬৩
    "একাদশ":   (169, 189),  # Book ১৬৪-১৮৪
    "দ্বাদশ":    (190, 202),  # Book ১৮৫-১৯৭
    "ত্রয়োদশ":  (203, 212),  # Book ১৯৮-২০৭
    "চতুর্দশ":   (213, 229),  # Book ২০৮-২২৪
    "পঞ্চদশ":    (230, 237),  # Book ২২৫-২৩২
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
        labeled = f"[ভূগোল | {chapter_label}]\n\n{chunk}"
        chunks.append(labeled)
    return chunks


def extract_chapter(ordinal_key, start_page, end_page):
    chapter_number, chapter_title = CHAPTERS["geography"][ordinal_key]
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
    print("NCTB Geography (ভূগোল ও পরিবেশ) OCR Extraction")
    print("=" * 60)

    if not os.path.exists(PDF_PATH):
        print(f"\nERROR: PDF not found at: {PDF_PATH}")
        print("Place the PDF in your project root.")
        return

    total = 0
    for ordinal_key, (start, end) in GEOGRAPHY_PAGES.items():
        if ordinal_key not in CHAPTERS["geography"]:
            print(f"⚠️  Skipping {ordinal_key} — not in chapters.py")
            continue
        total += extract_chapter(ordinal_key, start, end)

    print(f"\n{'=' * 60}")
    print(f"DONE! Total chunks: {total}")
    print(f"Chunks saved in: {OUTPUT_DIR}/chunks/")
    print("\nNext steps:")
    print("  1. python rag/embed.py       (re-embeds Biology + Geography)")
    print("  2. Update chain.py to pass subject parameter")


if __name__ == "__main__":
    main()
