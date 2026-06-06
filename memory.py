import json
import os
from datetime import datetime, timezone, timedelta

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

load_dotenv()

from chain import flash_llm, _transliterate_name_to_bangla

_flash = flash_llm | StrOutputParser()


def get_student_profile(user_id: str) -> dict | None:
    """Fetch student profile from Supabase. Returns None if not found."""
    try:
        from auth import get_admin_client
        res = get_admin_client().table('student_profiles').select('*').eq('user_id', user_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[memory] get error: {e}")
        return None


def _upsert(user_id: str, data: dict):
    try:
        from auth import get_admin_client
        admin = get_admin_client()
        data['updated_at'] = datetime.now(timezone.utc).isoformat()
        exists = admin.table('student_profiles').select('id').eq('user_id', user_id).execute()
        if exists.data:
            admin.table('student_profiles').update(data).eq('user_id', user_id).execute()
        else:
            data['user_id'] = user_id
            admin.table('student_profiles').insert(data).execute()
    except Exception as e:
        print(f"[memory] upsert error: {e}")


def save_preferred_name(user_id: str, name: str):
    """Persist the student's self-corrected name so it survives across new chats."""
    cleaned = name.strip().rstrip('.!?,।')
    if len(cleaned) >= 2:
        _upsert(user_id, {'preferred_name': cleaned})


def _merge(old: list, new: list, max_items: int = 15) -> list:
    """Deduplicate and keep the most recent items."""
    combined = list(dict.fromkeys((old or []) + (new or [])))
    return combined[-max_items:]


def _calculate_streak(last_date_str: str, current_streak: int) -> int:
    """Pure function — returns updated streak count given last session date and current streak."""
    if not last_date_str:
        return 1
    try:
        last_dt = datetime.fromisoformat(last_date_str.replace('Z', '+00:00'))
        days_ago = (datetime.now(timezone.utc) - last_dt).days
        if days_ago == 0:
            return max(current_streak or 1, 1)   # same day — no change
        elif days_ago == 1:
            return (current_streak or 1) + 1      # yesterday — extend streak
        else:
            return 1                              # gap — reset
    except Exception:
        return 1


def get_or_init_streak(user_id: str, profile: dict | None) -> int:
    """
    Calculate today's streak from an already-fetched profile.
    Schedules a background DB write if the value changed.
    Returns the new streak so the caller can return it immediately.
    NOTE: requires streak_days INTEGER DEFAULT 1 column in student_profiles.
    """
    import threading
    if not profile:
        return 1
    last_date = profile.get('last_session_date') or ''
    current = profile.get('streak_days') or 1
    new_streak = _calculate_streak(last_date, current)
    if new_streak != current:
        threading.Thread(
            target=_upsert,
            args=(user_id, {'streak_days': new_streak}),
            daemon=True,
        ).start()
    return new_streak


def analyze_conversation(messages: list) -> dict:
    """
    Use Flash to extract learning signals from recent conversation.
    Returns dict: topics_studied, weak_topics, strong_topics, confusion_signals
    """
    if not messages:
        return {}

    convo = "\n".join(
        f"{'ছাত্র' if m['role'] == 'user' else 'দীপ্তি'}: {str(m.get('content', ''))[:400]}"
        for m in messages[-20:]
        if isinstance(m.get('content'), str)
    )

    prompt = f"""নিচের কথোপকথন পড়ো এবং শুধু JSON দাও। অন্য কোনো text না।

{convo}

JSON format:
{{
  "topics_studied": ["পড়া হয়েছে এমন topic গুলো, বাংলায়"],
  "weak_topics": ["ছাত্র যেখানে আটকেছে বা ভুল করেছে, বাংলায়"],
  "strong_topics": ["ছাত্র যেটা ভালো বুঝেছে বা সঠিক উত্তর দিয়েছে, বাংলায়"],
  "confusion_signals": ["specific জিনিস যা ছাত্র বুঝতে পারেনি, বাংলায়"]
}}"""

    try:
        raw = _flash.invoke([HumanMessage(content=prompt)]).strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[memory] analyze error: {e}")
        return {}


def update_student_profile(user_id: str, messages: list):
    """
    Analyze recent conversation and merge learning signals into student profile.
    Called in background — never blocks the request.
    """
    signals = analyze_conversation(messages)
    if not signals:
        return

    try:
        from auth import get_admin_client
        res = get_admin_client().table('student_profiles').select('*').eq('user_id', user_id).execute()
        ex = res.data[0] if res.data else {}

        last_topic = (signals.get('topics_studied') or [''])[-1]
        update_data = {
            'weak_topics':        _merge(ex.get('weak_topics', []),       signals.get('weak_topics', [])),
            'strong_topics':      _merge(ex.get('strong_topics', []),     signals.get('strong_topics', [])),
            'confusion_signals':  _merge(ex.get('confusion_signals', []), signals.get('confusion_signals', [])),
            'session_count':      (ex.get('session_count') or 0) + 1,
            'total_messages':     (ex.get('total_messages') or 0) + len(messages),
        }
        if last_topic:
            update_data['last_session_topic'] = last_topic
            update_data['last_session_date'] = datetime.now(timezone.utc).isoformat()

        _upsert(user_id, update_data)
        _schedule_topics(user_id, signals.get('topics_studied') or [], ex)
        print(f"[memory] Updated profile for {user_id} — studied: {signals.get('topics_studied')}")

    except Exception as e:
        print(f"[memory] update error: {e}")


_GOODBYE_TRIGGERS = [
    "ধন্যবাদ", "thanks", "thank you", "শুক্রিয়া",
    "bye", "বিদায়", "যাচ্ছি", "রাখি", "আবার আসব", "আবার আসবো",
    "শেষ করি", "আজকের মতো", "পরে আসব", "পরে আসবো",
    "হয়েছে", "enough", "done", "শেষ",
]

def is_goodbye(user_input: str) -> bool:
    """Returns True when the student is wrapping up the session."""
    text = user_input.strip().lower()
    return any(t in text for t in _GOODBYE_TRIGGERS)


def generate_wrapup(messages: list, student_name: str = "") -> tuple[str, str]:
    """
    Generate a session wrap-up message + a short next-session promise.
    Returns (wrapup_message, promise_text).
    Only called when student says goodbye after a real session (6+ messages).
    """
    first = (student_name or "").strip().split()[0] if student_name and student_name.strip() else ""
    bangla_name = _transliterate_name_to_bangla(first) if first else ""
    name_call = bangla_name or "তুমি"

    convo = "\n".join(
        f"{'ছাত্র' if m['role'] == 'user' else 'দীপ্তি'}: {str(m.get('content', ''))[:300]}"
        for m in messages[-20:]
        if isinstance(m.get('content'), str)
    )

    prompt = f"""তুমি দীপ্তি আপু। আজকের পড়ার session শেষ হচ্ছে।

কথোপকথন:
{convo}

দুটো জিনিস দাও — শুধু JSON, অন্য কিছু না:

{{
  "wrapup": "২–৩ বাক্যে আজকের session summary। কী পড়েছে, কোথায় ভালো করেছে, কোথায় আরো কাজ দরকার। শেষে বলো পরের session-এ কী করব। Dipti-র voice-এ, {name_call}-কে address করো।",
  "promise": "এক লাইনে: পরের session-এ ঠিক কী করব। e.g. 'মাইটোসিসের ধাপগুলো quiz করব'"
}}"""

    try:
        raw = _flash.invoke([HumanMessage(content=prompt)]).strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        return data.get("wrapup", ""), data.get("promise", "")
    except Exception as e:
        print(f"[memory] wrapup error: {e}")
        return f"আজকের session শেষ! পরে আবার আসো, {name_call} 🌱", ""


_SUBJECT_NAMES = {
    "biology": "জীববিজ্ঞান", "physics": "পদার্থবিজ্ঞান",
    "chemistry": "রসায়ন", "geography": "ভূগোল",
    "accounting": "হিসাববিজ্ঞান", "math": "গণিত",
    "higher_math": "উচ্চতর গণিত", "bangla": "বাংলা সাহিত্য",
}
_KNOWN_SUBJECTS = set(_SUBJECT_NAMES.keys())


def generate_session_note(messages: list, subject: str) -> dict | None:
    """
    Produce a multi-section revision note covering all topics discussed in the chat.
    Returns {"title": str, "sections": [...]} or None.
    Each section: {heading, subject, definition, concepts, exam_tips}
    """
    print(f"[NOTE] generating for subject={subject} msgs={len(messages)}", flush=True)
    real_pairs = sum(
        1 for i in range(len(messages) - 1)
        if messages[i].get('role') == 'user' and messages[i + 1].get('role') == 'assistant'
        and isinstance(messages[i].get('content'), str)
        and not messages[i]['content'].startswith('__')
    )
    print(f"[NOTE] real_pairs={real_pairs}", flush=True)
    if real_pairs < 1:
        print(f"[NOTE] skipping — no real pairs", flush=True)
        return None

    convo = "\n".join(
        f"{'ছাত্র' if m['role'] == 'user' else 'দীপ্তি'}: {str(m.get('content', ''))[:250]}"
        for m in messages[-20:]
        if isinstance(m.get('content'), str) and not str(m.get('content', '')).startswith('__')
    )
    known_str = ", ".join(_KNOWN_SUBJECTS)
    prompt = (
        "এই পড়ার chat থেকে সব আলোচিত topic-এর জন্য একটি multi-section revision note তৈরি করো।\n\n"
        f"কথোপকথন:\n{convo}\n\n"
        "প্রতিটি আলাদা topic-এর জন্য একটি section তৈরি করো।\n"
        "শুধু JSON দাও, অন্য কিছু না:\n"
        '{{\n'
        '  "title": "২-৬ শব্দে বাংলা শিরোনাম (সব topic একসাথে)",\n'
        '  "sections": [\n'
        '    {{\n'
        '      "heading": "topic-এর বাংলা শিরোনাম",\n'
        f'      "subject": "এই topic-এর বিষয় ({known_str} থেকে একটি)",\n'
        '      "definition": "১-২ বাক্যে সংজ্ঞা",\n'
        '      "concepts": ["মূল fact ১", "মূল fact ২", "মূল fact ৩"],\n'
        '      "exam_tips": ["পরীক্ষার টিপস ১"]\n'
        '    }}\n'
        '  ]\n'
        '}}\n'
        "নিয়ম: প্রতি section-এ concepts: ৩-৫টি, exam_tips: ১-২টি। "
        "subject field অবশ্যই সঠিক হতে হবে — তরঙ্গ/physics topic হলে 'physics', জীববিজ্ঞান হলে 'biology'।"
    )
    raw = ""
    try:
        raw = _flash.invoke([HumanMessage(content=prompt)]).strip()
        print(f"[NOTE] LLM returned: {raw[:200]!r}", flush=True)
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        title = (data.get("title") or "").strip()
        sections = data.get("sections") or []
        if not title or not sections:
            print(f"[NOTE] parse failed — no title or empty sections", flush=True)
            return None
        # Validate and clean each section
        clean_sections = []
        for s in sections:
            if not isinstance(s, dict):
                continue
            heading = (s.get("heading") or "").strip()
            subj = (s.get("subject") or subject).strip().lower()
            if subj not in _KNOWN_SUBJECTS:
                subj = subject
            concepts = [p for p in (s.get("concepts") or []) if isinstance(p, str) and p.strip()]
            if not heading or not concepts:
                continue
            clean_sections.append({
                "heading":    heading,
                "subject":    subj,
                "definition": (s.get("definition") or "").strip(),
                "concepts":   concepts,
                "exam_tips":  [p for p in (s.get("exam_tips") or []) if isinstance(p, str) and p.strip()],
            })
        if not clean_sections:
            print(f"[NOTE] parse failed — no valid sections", flush=True)
            return None
        return {"title": title, "sections": clean_sections}
    except Exception as e:
        import traceback
        print(f"[NOTE] parse failed: {e}", flush=True)
        print(f"[NOTE] raw was: {raw[:400]!r}", flush=True)
        print(traceback.format_exc(), flush=True)
        return None


def save_note(user_id: str, chat_id: str, subject: str, title: str, sections: list):
    """Upsert one note per chat — insert on first answer, update as chat grows."""
    try:
        from auth import get_admin_client
        admin = get_admin_client()
        # Primary subject: use first section's detected subject (more accurate than passed subject)
        primary_subject = (sections[0].get("subject") or subject) if sections else subject
        print(f"[NOTE] upserting: title={title!r} chat_id={chat_id} primary_subject={primary_subject}", flush=True)
        note_data = {
            "user_id": user_id,
            "subject": primary_subject,
            "chapter": primary_subject,
            "title": title,
            "points": {"v": 2, "sections": sections},
        }

        # Try upsert by chat_id (requires SQL migration: ALTER TABLE notes ADD COLUMN IF NOT EXISTS chat_id text)
        existing = None
        if chat_id:
            try:
                existing = admin.table("notes").select("id").eq("user_id", user_id).eq("chat_id", chat_id).execute()
            except Exception:
                pass  # column not yet added — fall through to plain insert

        if existing and existing.data:
            result = admin.table("notes").update(note_data).eq("id", existing.data[0]["id"]).execute()
            print(f"[memory] note updated: {title!r}", flush=True)
        else:
            # Try insert with chat_id; fall back to without if column missing
            if chat_id:
                try:
                    result = admin.table("notes").insert({**note_data, "chat_id": chat_id}).execute()
                except Exception:
                    result = admin.table("notes").insert(note_data).execute()
                    print(f"[NOTE] inserted without chat_id (run migration to enable upsert)", flush=True)
            else:
                result = admin.table("notes").insert(note_data).execute()
            print(f"[memory] note created: {title!r}", flush=True)

        if not result.data:
            print(f"[NOTE] upsert returned no data", flush=True)
    except Exception as e:
        import traceback
        print(f"[NOTE] save_note EXCEPTION: {e}", flush=True)
        print(traceback.format_exc(), flush=True)


def touch_session_date(user_id: str):
    """Update last_session_date to now without touching anything else."""
    _upsert(user_id, {'last_session_date': datetime.now(timezone.utc).isoformat()})


def save_quiz_result(user_id: str, score: int, total: int):
    """Persist last quiz score so the opening message can reference it."""
    _upsert(user_id, {
        'last_quiz_score': score,
        'last_quiz_total': total,
        'last_quiz_date': datetime.now(timezone.utc).isoformat(),
    })
    print(f"[memory] Quiz result saved: {score}/{total} for {user_id}")


def save_last_stream(user_id: str, stream: str):
    """Persist stream permanently to profile (survives device changes) and as last-used."""
    if stream:
        _upsert(user_id, {'last_stream': stream, 'stream': stream})


def save_session_promise(user_id: str, promise: str, messages: list):
    """Save next-session promise + update full profile after a session ends."""
    try:
        signals = analyze_conversation(messages)
        from auth import get_admin_client
        res = get_admin_client().table('student_profiles').select('*').eq('user_id', user_id).execute()
        ex = res.data[0] if res.data else {}

        last_topic = (signals.get('topics_studied') or [''])[-1] if signals else ''
        update_data = {
            'next_session_promise': promise,
            'weak_topics':       _merge(ex.get('weak_topics', []),       signals.get('weak_topics', []) if signals else []),
            'strong_topics':     _merge(ex.get('strong_topics', []),     signals.get('strong_topics', []) if signals else []),
            'confusion_signals': _merge(ex.get('confusion_signals', []), signals.get('confusion_signals', []) if signals else []),
            'session_count':     (ex.get('session_count') or 0) + 1,
            'total_messages':    (ex.get('total_messages') or 0) + len(messages),
        }
        if last_topic:
            update_data['last_session_topic'] = last_topic
            update_data['last_session_date'] = datetime.now(timezone.utc).isoformat()

        _upsert(user_id, update_data)
        if signals:
            _schedule_topics(user_id, signals.get('topics_studied') or [], ex)
        print(f"[memory] Session saved. Promise: {promise}")
    except Exception as e:
        print(f"[memory] save_session_promise error: {e}")


_INTERVALS = [1, 3, 7, 14, 30, 60]  # spaced repetition intervals in days


def _today() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _schedule_topics(user_id: str, topics: list, profile: dict | None):
    """Add newly studied topics to the spaced repetition schedule (background-safe)."""
    if not topics:
        return
    existing = list((profile or {}).get('topic_schedule') or [])
    existing_names = {e['topic'] for e in existing}
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d')
    added = [
        {'topic': t, 'next_review': tomorrow, 'interval': 1, 'count': 0}
        for t in topics if t and t not in existing_names
    ]
    if not added:
        return
    updated = (existing + added)[-20:]
    try:
        _upsert(user_id, {'topic_schedule': updated})
    except Exception as e:
        print(f"[memory] schedule_topics error: {e}")


def get_due_reviews(profile: dict | None) -> list[dict]:
    """Return topic schedule entries due today or overdue, most overdue first."""
    if not profile:
        return []
    schedule = profile.get('topic_schedule') or []
    today = _today()
    due = [e for e in schedule if e.get('next_review', '9999') <= today]
    due.sort(key=lambda e: e.get('next_review', '9999'))
    return due[:3]


def mark_topic_reviewed(user_id: str, topic: str, profile: dict | None):
    """Advance a topic's interval after the student reviews it."""
    schedule = list((profile or {}).get('topic_schedule') or [])
    for entry in schedule:
        if entry.get('topic') == topic:
            count = entry.get('count', 0) + 1
            new_interval = _INTERVALS[min(count, len(_INTERVALS) - 1)]
            entry['count'] = count
            entry['interval'] = new_interval
            entry['next_review'] = (datetime.now(timezone.utc) + timedelta(days=new_interval)).strftime('%Y-%m-%d')
            break
    try:
        _upsert(user_id, {'topic_schedule': schedule})
    except Exception as e:
        print(f"[memory] mark_reviewed error: {e}")


def get_dipti_opening(profile: dict | None, student_name: str = "", current_stream: str = "") -> str:
    """
    Generate a short personalised opening message for a new chat session.
    References last topic and weak areas if profile exists.
    """
    first = (student_name or "").strip().split()[0] if student_name and student_name.strip() else ""
    bangla_name = _transliterate_name_to_bangla(first) if first else ""
    name_call = bangla_name or ""

    if not profile:
        return f"হ্যালো{', ' + name_call if name_call else ''}! আজকে কোন বিষয়টা নিয়ে পড়তে চাও? 🌱"

    last_topic      = profile.get('last_session_topic') or ''
    weak_topics     = profile.get('weak_topics') or []
    session_count   = profile.get('session_count') or 0
    promise         = profile.get('next_session_promise') or ''
    last_date_str   = profile.get('last_session_date') or ''
    streak_days     = profile.get('streak_days') or 1
    quiz_score      = profile.get('last_quiz_score')
    quiz_total      = profile.get('last_quiz_total')
    quiz_ref        = f"{quiz_score}/{quiz_total} সঠিক" if quiz_score is not None and quiz_total else ''

    # Calculate human-readable time since last session
    time_ref = "আগে"
    if last_date_str:
        try:
            last_dt = datetime.fromisoformat(last_date_str.replace('Z', '+00:00'))
            diff = datetime.now(timezone.utc) - last_dt
            minutes = int(diff.total_seconds() / 60)
            if minutes < 60:
                time_ref = f"{minutes} মিনিট আগে"
            elif minutes < 120:
                time_ref = "একটু আগে (এই session-এ)"
            elif minutes < 1440:
                time_ref = "আজকে আগে"
            elif minutes < 2880:
                time_ref = "গতকাল"
            else:
                days = minutes // 1440
                time_ref = f"{days} দিন আগে"
        except Exception:
            pass

    stream_names = {'science': 'বিজ্ঞান', 'commerce': 'ব্যবসায় শিক্ষা', 'arts': 'মানবিক'}
    stream_subjects = {
        'science': ['জীববিজ্ঞান', 'পদার্থ', 'রসায়ন', 'biology', 'physics', 'chemistry', 'সালোকসংশ্লেষণ', 'কোষ', 'তরঙ্গ', 'ভৌত', 'ফলিত'],
        'commerce': ['হিসাব', 'জাবেদা', 'ব্যবসা', 'অর্থনীতি', 'accounting', 'finance', 'বাণিজ্য'],
        'arts': ['ভূগোল', 'ইতিহাস', 'পৌরনীতি', 'geography', 'পরিবেশ', 'মানবিক', 'নাগরিক'],
    }
    current_stream_name = stream_names.get(current_stream, '') if current_stream else ''

    def _belongs_to_other_stream(text: str) -> bool:
        """Returns True if text contains keywords from a stream other than current."""
        if not current_stream or not text:
            return False
        t = text.lower()
        other_kws = [s for st, subjs in stream_subjects.items() if st != current_stream for s in subjs]
        current_kws = stream_subjects.get(current_stream, [])
        has_other = any(k.lower() in t for k in other_kws)
        has_current = any(k.lower() in t for k in current_kws)
        return has_other and not has_current

    # Filter profile data to current stream — prevents biology bleeding into geography etc.
    if current_stream:
        current_kws = stream_subjects.get(current_stream, [])
        # Keep only weak topics that match current stream
        weak_topics = [t for t in weak_topics if any(k.lower() in t.lower() for k in current_kws)]
        # Clear last_topic, promise, quiz_ref if they belong to another stream
        if _belongs_to_other_stream(last_topic):
            last_topic = ''
            quiz_ref = ''  # quiz was in another stream, don't reference it here
        if _belongs_to_other_stream(promise):
            promise = ''

    streak_ref = f"{streak_days} দিন ধরে" if streak_days >= 3 else ''

    prompt = f"""তুমি দীপ্তি আপু — SSC ছাত্রের AI শিক্ষিকা। নতুন chat শুরুতে ছাত্রকে ১–২ বাক্যে স্বাগত জানাও।

ছাত্রের নাম: {name_call or 'তুমি'}
বর্তমান বিভাগ: {current_stream_name or 'অজানা'}
আগের session কখন ছিল: {time_ref}
আগের session-এর শেষ topic: {last_topic or 'নেই'}
গত session-এ দেওয়া promise: {promise or 'নেই'}
গত quiz-এর ফলাফল: {quiz_ref or 'নেই'}
দুর্বল topic: {', '.join(weak_topics[-2:]) if weak_topics else 'নেই'}
মোট session: {session_count}
পরপর পড়ার streak: {streak_ref or 'নেই'}

নিয়ম:
- সময়ের reference সঠিক রাখো — "গতকাল" বলো না যদি session আজকেই হয়ে থাকে
- promise থাকলে সেটা দিয়ে শুরু করো, না হলে আগের topic উল্লেখ করো
- quiz ফলাফল থাকলে এবং score কম হলে (৭০%-এর নিচে) সেই topic আরেকবার practice-এর offer করো
- streak ৩+ দিন হলে একবার mention করো ("X দিন ধরে পড়ছ — দারুণ!")
- শুধুমাত্র বর্তমান বিভাগের বিষয় নিয়ে কথা বলো — অন্য বিভাগের topic উল্লেখ করবে না
- প্রথম session হলে friendly শুরু করো
- শেষে একটা প্রশ্ন
- ১–২ বাক্যের বেশি না, natural বাংলা"""

    try:
        return _flash.invoke([HumanMessage(content=prompt)]).strip()
    except Exception as e:
        print(f"[memory] opening error: {e}")
        return f"হ্যালো{', ' + name_call if name_call else ''}! আজকে কী পড়বে? 🌱"
