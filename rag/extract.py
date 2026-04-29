import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import os
import re

# ── Windows paths ───────────────────────────────────────────────
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\poppler\Library\bin"
# ────────────────────────────────────────────────────────────────

PDF_PATH = r"Secondary (BV)-2026_Class 9-10_Biology_compressed.pdf"
OUTPUT_DIR = "biology_chapters"
DPI = 200

# Chapter map from table of contents
CHAPTERS = {
    "প্রথম":     ("জীবনপাঠ",                            6,  16),
    "দ্বিতীয়":  ("জীবকোষ ও টিস্যু",                   17,  49),
    "তৃতীয়":    ("কোষ বিভাজন",                         50,  63),
    "চতুর্থ":    ("জীবনীশক্তি",                          64,  83),
    "পঞ্চম":     ("খাদ্য পুষ্টি এবং পরিপাক",            84, 124),
    "ষষ্ঠ":      ("জীবে পরিবহণ",                        125, 159),
    "সপ্তম":     ("গ্যাসীয় বিনিময়",                    160, 177),
    "অষ্টম":     ("রেচন প্রক্রিয়া",                     178, 189),
    "নবম":       ("দৃঢ়তা প্রদান ও চলন",                190, 203),
    "দশম":       ("সমন্বয়",                              204, 230),
    "একাদশ":    ("জীবের প্রজনন",                        231, 253),
    "দ্বাদশ":    ("জীবের বংশগতি ও জৈব অভিব্যক্তি",     254, 281),
    "ত্রয়োদশ":  ("জীবের পরিবেশ",                       282, 304),
    "চতুর্দশ":   ("জীবপ্রযুক্তি",                        305, 320),
}

def ocr_page(pil_image):
    return pytesseract.image_to_string(pil_image, lang="ben+eng")

def clean_text(text):
    text = re.sub(r"^\s*[০-৯\d]+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^\s*২০২৬\s*$", "", text, flags=re.MULTILINE)
    return text.strip()

def chunk_text(text, chapter_label, max_words=400):
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunk = " ".join(words[i : i + max_words])
        labeled = f"[জীববিজ্ঞান | {chapter_label}]\n\n{chunk}"
        chunks.append(labeled)
    return chunks

def extract_chapter(chapter_name, chapter_title, start_page, end_page):
    print(f"\n→ {chapter_name} অধ্যায়: {chapter_title}  (pages {start_page}–{end_page})")

    all_text = f"{chapter_name} অধ্যায়: {chapter_title}\n\n"

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

    # Save full chapter
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe = chapter_name.replace(" ", "_")
    chapter_file = os.path.join(OUTPUT_DIR, f"{safe}_{chapter_title[:20]}.txt")
    with open(chapter_file, "w", encoding="utf-8") as f:
        f.write(all_text)

    # Save chunks
    chunks_dir = os.path.join(OUTPUT_DIR, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)
    chunks = chunk_text(all_text, f"{chapter_name} অধ্যায় - {chapter_title}")
    for j, chunk in enumerate(chunks):
        with open(os.path.join(chunks_dir, f"{safe}_chunk_{j+1}.txt"), "w", encoding="utf-8") as f:
            f.write(chunk)

    print(f"   Saved {len(chunks)} chunks")
    return len(chunks)

def main():
    print("=" * 50)
    print("NCTB Biology OCR Extraction")
    print("=" * 50)

    # Quick check before starting
    if not os.path.exists(PDF_PATH):
        print(f"\nERROR: PDF not found at: {PDF_PATH}")
        print("Make sure the PDF is in your bangla-edtech/ folder")
        return

    total = 0
    for chapter_name, (title, start, end) in CHAPTERS.items():
        total += extract_chapter(chapter_name, title, start, end)

    print(f"\n{'='*50}")
    print(f"DONE! Total chunks: {total}")
    print(f"Chunks saved in: {OUTPUT_DIR}/chunks/")
    print("Next step: run  python rag/embed.py")

if __name__ == "__main__":
    main()