"""
Extract NCTB SSC বাংলা সাহিত্য literary pieces via OCR.

25 prose (গদ্য) pieces + 27 poetry (কবিতা) pieces.
PDF page = book page + 5 (verified: PDF page 6 = book page 1).
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

PDF_PATH = r"Secondary (BV)-2026_Class 9-10_Bangla Sahitto_compressed (1).pdf"
OUTPUT_DIR = "books/ssc/bangla"
DPI = 200

# PDF page = book page + 5
# (prose_key, start_pdf, end_pdf, section_type)
BANGLA_PAGES = {
    # গদ্য (Prose)
    "g01": (6,   10,  "গদ্য"),   # প্রতুপকার
    "g02": (11,  15,  "গদ্য"),   # ফুলের বিবাহ
    "g03": (16,  22,  "গদ্য"),   # সুভা
    "g04": (23,  25,  "গদ্য"),   # লাইব্রেরি
    "g05": (26,  31,  "গদ্য"),   # বই পড়া
    "g06": (32,  41,  "গদ্য"),   # অভাগীর স্বর্গ
    "g07": (42,  46,  "গদ্য"),   # নিরীহ বাঙালি
    "g08": (47,  53,  "গদ্য"),   # পল্লীসাহিত্য
    "g09": (54,  59,  "গদ্য"),   # উদ্যম ও পরিশ্রম
    "g10": (60,  62,  "গদ্য"),   # জীবনে শিল্পের স্থান
    "g11": (63,  70,  "গদ্য"),   # আম-আঁটির ভেঁপু
    "g12": (71,  77,  "গদ্য"),   # মানুষ মুহম্মদ (স.)
    "g13": (78,  81,  "গদ্য"),   # উপেক্ষিত শক্তির উদ্বোধন
    "g14": (82,  84,  "গদ্য"),   # নিমগাছ
    "g15": (85,  88,  "গদ্য"),   # শিক্ষা ও মনুষ্যত্ব
    "g16": (89,  96,  "গদ্য"),   # প্রবাস বন্ধু
    "g17": (97,  104, "গদ্য"),   # মমতাদি
    "g18": (105, 111, "গদ্য"),   # বনমানুষ
    "g19": (112, 118, "গদ্য"),   # একাত্তরের দিনগুলি
    "g20": (119, 128, "গদ্য"),   # স্বাধীনতা আমার স্বাধীনতা
    "g21": (129, 135, "গদ্য"),   # একুশের গল্প
    "g22": (136, 140, "গদ্য"),   # আমাদের সংস্কৃতি
    "g23": (141, 148, "গদ্য"),   # সাহিত্যের রূপ ও রীতি
    "g24": (149, 152, "গদ্য"),   # বাংলা শব্দ
    "g25": (153, 159, "গদ্য"),   # আমাদের নতুন গৌরবগাথা
    # কবিতা (Poetry)
    "k01": (160, 162, "কবিতা"),  # বন্দনা
    "k02": (163, 165, "কবিতা"),  # হামদ্
    "k03": (166, 168, "কবিতা"),  # বঙ্গবাণী
    "k04": (169, 171, "কবিতা"),  # কপোতাক্ষ নদ
    "k05": (172, 175, "কবিতা"),  # জীবন-সঙ্গীত
    "k06": (176, 178, "কবিতা"),  # প্রাণ
    "k07": (179, 186, "কবিতা"),  # জুতা-আবিষ্কার
    "k08": (187, 190, "কবিতা"),  # ঝরনার গান
    "k09": (191, 193, "কবিতা"),  # ছায়াবাজি
    "k10": (194, 197, "কবিতা"),  # জীবন বিনিময়
    "k11": (198, 200, "কবিতা"),  # মানুষ
    "k12": (201, 206, "কবিতা"),  # উমর ফারুক
    "k13": (207, 209, "কবিতা"),  # সেইদিন এই মাঠ
    "k14": (210, 214, "কবিতা"),  # যাব আমি তোমার দেশে
    "k15": (215, 217, "কবিতা"),  # একটি কবিতা
    "k16": (218, 221, "কবিতা"),  # আমার দেশ
    "k17": (222, 225, "কবিতা"),  # আমি কোনো আগন্তুক নই
    "k18": (226, 228, "কবিতা"),  # বৃষ্টি
    "k19": (229, 232, "কবিতা"),  # মে-দিনের কবিতা
    "k20": (233, 235, "কবিতা"),  # আশা
    "k21": (236, 237, "কবিতা"),  # পোস্টার
    "k22": (238, 241, "কবিতা"),  # রানার
    "k23": (242, 245, "কবিতা"),  # তোমাকে পাওয়ার জন্যে, হে স্বাধীনতা
    "k24": (246, 249, "কবিতা"),  # অবাক সূর্যোদয়
    "k25": (250, 253, "কবিতা"),  # বোশেখ
    "k26": (254, 257, "কবিতা"),  # চুনিয়া আমার আর্কেডিয়া
    "k27": (258, 262, "কবিতা"),  # মিছিল
}


def ocr_page(pil_image):
    return pytesseract.image_to_string(pil_image, lang="ben+eng")


def clean_text(text):
    text = re.sub(r"^\s*[০-৯\d]+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^\s*২০২৬\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*২০২৫\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


def chunk_text(text, piece_label, max_words=400):
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunk = " ".join(words[i : i + max_words])
        labeled = f"[বাংলা সাহিত্য | {piece_label}]\n\n{chunk}"
        chunks.append(labeled)
    return chunks


def extract_piece(piece_key, start_page, end_page, section_type):
    chapter_number, piece_title = CHAPTERS["bangla"][piece_key]
    print(f"\n→ {section_type} {chapter_number}: {piece_title}  (PDF pages {start_page}–{end_page})")

    all_text = f"{section_type}: {piece_title}\n\n"

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
    piece_file = os.path.join(OUTPUT_DIR, f"{piece_key}_{piece_title[:25]}.txt")
    with open(piece_file, "w", encoding="utf-8") as f:
        f.write(all_text)

    chunks_dir = os.path.join(OUTPUT_DIR, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)
    piece_label = f"{section_type} - {piece_title}"
    chunks = chunk_text(all_text, piece_label)
    for j, chunk in enumerate(chunks, start=1):
        out_path = os.path.join(chunks_dir, f"{piece_key}_chunk_{j}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(chunk)

    print(f"   Saved {len(chunks)} chunks")
    return len(chunks)


def main():
    print("=" * 60)
    print("NCTB বাংলা সাহিত্য OCR Extraction")
    print("=" * 60)

    if not os.path.exists(PDF_PATH):
        print(f"\nERROR: PDF not found at: {PDF_PATH}")
        print("Set PDF_PATH at the top of this file to your actual filename.")
        return

    total = 0
    for piece_key, (start, end, section) in BANGLA_PAGES.items():
        if piece_key not in CHAPTERS["bangla"]:
            print(f"⚠️  Skipping {piece_key} — not in chapters.py")
            continue
        total += extract_piece(piece_key, start, end, section)

    print(f"\n{'=' * 60}")
    print(f"DONE! Total chunks: {total}")
    print(f"Chunks saved in: {OUTPUT_DIR}/chunks/")
    print("\nNext steps:")
    print("  1. python rag/embed.py --subjects bangla")
    print("  2. Restart app.py")


if __name__ == "__main__":
    main()
