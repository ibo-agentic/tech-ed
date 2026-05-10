"""
Chapter registry for all NCTB SSC subjects.
Single source of truth — extract scripts and embed.py both read from here.

VERIFIED against actual NCTB textbook tables of contents.
"""

CHAPTERS = {
    "biology": {
        "প্রথম":     (1,  "জীবনপাঠ"),
        "দ্বিতীয়":   (2,  "জীবকোষ ও টিস্যু"),
        "তৃতীয়":    (3,  "কোষ বিভাজন"),
        "চতুর্থ":    (4,  "জীবনীশক্তি"),
        "পঞ্চম":     (5,  "খাদ্য, পুষ্টি ও পরিপাক"),
        "ষষ্ঠ":      (6,  "জীবে পরিবহণ"),
        "সপ্তম":    (7,  "গ্যাসীয় বিনিময়"),
        "অষ্টম":    (8,  "রেচন প্রক্রিয়া"),
        "নবম":      (9,  "দৃঢ়তা প্রদান ও চলন"),
        "দশম":      (10, "সমন্বয়"),
        "একাদশ":   (11, "জীবের প্রজনন"),
        "দ্বাদশ":    (12, "জীবের বংশগতি ও জৈব অভিব্যক্তি"),
        "ত্রয়োদশ":  (13, "জীবের পরিবেশ"),
        "চতুর্দশ":   (14, "জীবপ্রযুক্তি"),
    },

    # Verified from NCTB SSC ভূগোল ও পরিবেশ (Geography & Environment) 2026 edition
    "geography": {
        "প্রথম":     (1,  "ভূগোল ও পরিবেশ"),
        "দ্বিতীয়":   (2,  "মহাবিশ্ব ও আমাদের পৃথিবী"),
        "তৃতীয়":    (3,  "মানচিত্র পঠন ও ব্যবহার"),
        "চতুর্থ":    (4,  "পৃথিবীর অভ্যন্তরীণ ও বাহ্যিক গঠন"),
        "পঞ্চম":     (5,  "বায়ুমণ্ডল"),
        "ষষ্ঠ":      (6,  "বারিমণ্ডল"),
        "সপ্তম":    (7,  "জনসংখ্যা"),
        "অষ্টম":    (8,  "মানববসতি"),
        "নবম":      (9,  "সম্পদ ও অর্থনৈতিক কার্যাবলি"),
        "দশম":      (10, "বাংলাদেশের ভৌগোলিক বিবরণ"),
        "একাদশ":   (11, "বাংলাদেশের সম্পদ ও শিল্প"),
        "দ্বাদশ":    (12, "বাংলাদেশের যোগাযোগ ব্যবস্থা ও বাণিজ্য"),
        "ত্রয়োদশ":  (13, "বাংলাদেশের উন্নয়ন কর্মকাণ্ড ও পরিবেশের ভারসাম্য"),
        "চতুর্দশ":   (14, "বাংলাদেশের প্রাকৃতিক দুর্যোগ"),
        "পঞ্চদশ":    (15, "টেকসই উন্নয়ন-অভীষ্ট (এসডিজি)"),
    },
}

SUBJECT_STREAM = {
    "biology":   "science",
    "physics":   "science",
    "chemistry": "science",
    "geography": "arts",
    "history":   "arts",
    "civics":    "arts",
    "accounting": "commerce",
}


def parse_chunk_filename(filename: str, subject: str):
    """Parse a chunk filename like 'পঞ্চম_chunk_3.txt' into clean metadata."""
    stem = filename.replace(".txt", "")
    parts = stem.split("_chunk_")
    if len(parts) != 2:
        return None

    ordinal_key = parts[0]
    chunk_num = parts[1]

    chapters = CHAPTERS.get(subject, {})
    if ordinal_key not in chapters:
        return None

    chapter_number, chapter_title = chapters[ordinal_key]

    return {
        "subject": subject,
        "stream": SUBJECT_STREAM.get(subject, "unknown"),
        "chapter_number": chapter_number,
        "chapter": f"অধ্যায় {chapter_number}: {chapter_title}",
        "chapter_title": chapter_title,
        "chunk_id": f"{subject}_ch{chapter_number}_chunk_{chunk_num}",
    }