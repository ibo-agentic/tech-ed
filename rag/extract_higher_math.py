"""
Extract NCTB SSC উচ্চতর গণিত (Higher Math) chapters via OCR.

Verified TOC (14 chapters). Assumes book page + 5 = PDF page.
⚠️  Adjust PDF_PATH and the offset (+5) if your PDF has different front matter.
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

PDF_PATH = r"Secondary (BV)-2026_Class 9-10_Higher Math_compressed.pdf"
OUTPUT_DIR = "books/ssc/higher_math"
DPI = 200

# PDF page = book page + 5  (5 pages of front matter before page 1)
# End page = start page of NEXT chapter - 1
HIGHER_MATH_PAGES = {
    "প্রথম":     (6,   42),   # Book ১–৩৭    সেট ও ফাংশন
    "দ্বিতীয়":   (43,  67),   # Book ৩৮–৬২   বীজগাণিতিক রাশি
    "তৃতীয়":    (68,  86),   # Book ৬৩–৮১   জ্যামিতি
    "চতুর্থ":    (87,  100),  # Book ৮২–৯৫   জ্যামিতিক অঙ্কন
    "পঞ্চম":     (101, 127),  # Book ৯৬–১২২  সমীকরণ
    "ষষ্ঠ":      (128, 140),  # Book ১২৩–১৩৫ অসমতা
    "সপ্তম":    (141, 150),  # Book ১৩৬–১৪৫ অসীম ধারা
    "অষ্টম":    (151, 197),  # Book ১৪৬–১৯২ ত্রিকোণমিতি
    "নবম":      (198, 227),  # Book ১৯৩–২২২ সূচকীয় ও লগারিদমীয় ফাংশন
    "দশম":      (228, 243),  # Book ২২৩–২৩৮ দ্বিপদী বিস্তৃতি
    "একাদশ":   (244, 275),  # Book ২৩৯–২৭০ স্থানাঙ্ক জ্যামিতি
    "দ্বাদশ":    (276, 291),  # Book ২৭১–২৮৬ সমতলীয় ভেক্টর
    "ত্রয়োদশ":  (292, 310),  # Book ২৮৭–৩০৫ ঘন জ্যামিতি
    "চতুর্দশ":   (311, 332),  # Book ৩০৬–৩২৭ সম্ভাবনা
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
        labeled = f"[উচ্চতর গণিত | {chapter_label}]\n\n{chunk}"
        chunks.append(labeled)
    return chunks


def extract_chapter(ordinal_key, start_page, end_page):
    chapter_number, chapter_title = CHAPTERS["higher_math"][ordinal_key]
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
    print("NCTB উচ্চতর গণিত (Higher Math) OCR Extraction")
    print("=" * 60)

    if not os.path.exists(PDF_PATH):
        print(f"\nERROR: PDF not found at: {PDF_PATH}")
        print("Set PDF_PATH at the top of this file to your actual filename.")
        return

    total = 0
    for ordinal_key, (start, end) in HIGHER_MATH_PAGES.items():
        if ordinal_key not in CHAPTERS["higher_math"]:
            print(f"⚠️  Skipping {ordinal_key} — not in chapters.py")
            continue
        total += extract_chapter(ordinal_key, start, end)

    print(f"\n{'=' * 60}")
    print(f"DONE! Total chunks: {total}")
    print(f"Chunks saved in: {OUTPUT_DIR}/chunks/")
    print("\nNext steps:")
    print("  1. python rag/embed.py --subjects higher_math")
    print("  2. Restart app.py")


if __name__ == "__main__":
    main()
