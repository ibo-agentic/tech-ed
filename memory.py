import json
import os
from datetime import datetime, timezone

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


def _merge(old: list, new: list, max_items: int = 15) -> list:
    """Deduplicate and keep the most recent items."""
    combined = list(dict.fromkeys((old or []) + (new or [])))
    return combined[-max_items:]


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
    """Record which stream was active so openings don't bleed across streams."""
    if stream:
        _upsert(user_id, {'last_stream': stream})


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
        print(f"[memory] Session saved. Promise: {promise}")
    except Exception as e:
        print(f"[memory] save_session_promise error: {e}")


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

    prompt = f"""তুমি দীপ্তি আপু — SSC ছাত্রের AI শিক্ষিকা। নতুন chat শুরুতে ছাত্রকে ১–২ বাক্যে স্বাগত জানাও।

ছাত্রের নাম: {name_call or 'তুমি'}
বর্তমান বিভাগ: {current_stream_name or 'অজানা'}
আগের session কখন ছিল: {time_ref}
আগের session-এর শেষ topic: {last_topic or 'নেই'}
গত session-এ দেওয়া promise: {promise or 'নেই'}
গত quiz-এর ফলাফল: {quiz_ref or 'নেই'}
দুর্বল topic: {', '.join(weak_topics[-2:]) if weak_topics else 'নেই'}
মোট session: {session_count}

নিয়ম:
- সময়ের reference সঠিক রাখো — "গতকাল" বলো না যদি session আজকেই হয়ে থাকে
- promise থাকলে সেটা দিয়ে শুরু করো, না হলে আগের topic উল্লেখ করো
- quiz ফলাফল থাকলে এবং score কম হলে (৭০%-এর নিচে) সেই topic আরেকবার practice-এর offer করো
- শুধুমাত্র বর্তমান বিভাগের বিষয় নিয়ে কথা বলো — অন্য বিভাগের topic উল্লেখ করবে না
- প্রথম session হলে friendly শুরু করো
- শেষে একটা প্রশ্ন
- ১–২ বাক্যের বেশি না, natural বাংলা"""

    try:
        return _flash.invoke([HumanMessage(content=prompt)]).strip()
    except Exception as e:
        print(f"[memory] opening error: {e}")
        return f"হ্যালো{', ' + name_call if name_call else ''}! আজকে কী পড়বে? 🌱"
