import sys
import io
import os

# Force UTF-8 for all console output on Windows (prevents charmap errors from emoji/Bengali)
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, render_template, request, jsonify, session, Response, stream_with_context, send_from_directory
from dotenv import load_dotenv
from chain import get_answer
from auth import auth_bp, login_required, check_message_limit, increment_message_count, get_admin_client
from image_chain import get_answer_with_image
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import base64
import os
import threading
import json
from datetime import datetime, timedelta, timezone

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "bangla-edtech_2026")

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)
app.register_blueprint(auth_bp)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)

@app.errorhandler(429)
def rate_limit_handler(_e):
    return jsonify(error="অনেক বেশি অনুরোধ করা হয়েছে। একটু অপেক্ষা করো।"), 429


# ── CONSTANTS ──
MAX_INSTRUCTIONS_LENGTH = 1000


# ── CHAT HELPERS ──

def get_or_create_chat(user_id, chat_id=None, project_id=None, subject='biology'):
    admin = get_admin_client()

    if chat_id:
        res = admin.table('chats').select('*').eq('id', chat_id).eq('user_id', user_id).execute()
        if res.data:
            return res.data[0]

    new_chat_data = {
        'user_id': user_id,
        'title': 'New Chat',
        'messages': [],
        'subject': subject,
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    if project_id:
        new_chat_data['project_id'] = project_id

    try:
        new_chat = admin.table('chats').insert(new_chat_data).execute()
    except Exception:
        # Column may not exist yet — retry without subject
        new_chat_data.pop('subject', None)
        new_chat = admin.table('chats').insert(new_chat_data).execute()
    return new_chat.data[0]


def save_messages(chat_id, messages):
    admin = get_admin_client()
    admin.table('chats').update({
        'messages': messages,
        'updated_at': datetime.now(timezone.utc).isoformat()
    }).eq('id', chat_id).execute()


_DEFAULT_IMAGE_CAPTION = "এই ছবিটি দেখে বুঝিয়ে দাও।"

def auto_title(chat_id, user_message):
    try:
        msg = user_message.strip()
        if len(msg) < 10 or msg == _DEFAULT_IMAGE_CAPTION:
            return
        admin = get_admin_client()
        current = admin.table('chats').select('title').eq('id', chat_id).execute()
        if current.data and current.data[0].get('title', 'New Chat') != 'New Chat':
            return
        title = msg[:50] + ('...' if len(msg) > 50 else '')
        admin.table('chats').update({'title': title}).eq('id', chat_id).execute()
    except Exception:
        pass


def auto_title_image(chat_id, subject: str, answer: str):
    """Generate a meaningful title for image chats from detected subject + answer."""
    try:
        admin = get_admin_client()
        current = admin.table('chats').select('title').eq('id', chat_id).execute()
        if current.data and current.data[0].get('title', 'New Chat') != 'New Chat':
            return
        subject_map = {
            'biology': 'জীববিজ্ঞান', 'physics': 'পদার্থবিজ্ঞান',
            'chemistry': 'রসায়ন', 'math': 'গণিত',
            'accounting': 'হিসাববিজ্ঞান', 'geography': 'ভূগোল',
        }
        subj_label = subject_map.get(subject, subject.title())
        # Find first meaningful sentence in the answer (skip headers/tables/empty lines)
        for line in answer.split('\n'):
            line = line.strip().lstrip('#*>|- ').strip()
            if len(line) > 20 and not line.startswith('|') and not line.startswith('---'):
                snippet = line[:40] + ('...' if len(line) > 40 else '')
                title = f"{subj_label}: {snippet}"
                admin.table('chats').update({'title': title}).eq('id', chat_id).execute()
                return
        admin.table('chats').update({'title': subj_label}).eq('id', chat_id).execute()
    except Exception:
        pass


def background_save(chat_id, messages, user_message, user_id):
    try:
        save_messages(chat_id, messages)
        auto_title(chat_id, user_message)
        increment_message_count(user_id)
        # Always stamp the session date so opening message time-ref stays accurate
        if user_id:
            from memory import touch_session_date
            touch_session_date(user_id)
        # Full profile analysis every 10 messages
        if user_id and len(messages) % 10 == 0:
            from memory import update_student_profile
            update_student_profile(user_id, messages)
    except Exception as e:
        print(f"Background save error: {e}")


def get_project_instructions(project_id, user_id):
    """Fetch custom instructions for a project. Returns empty string if none."""
    if not project_id:
        return ""
    try:
        admin = get_admin_client()
        res = admin.table('projects').select('custom_instructions').eq('id', project_id).eq('user_id', user_id).execute()
        if res.data and res.data[0].get('custom_instructions'):
            return res.data[0]['custom_instructions']
    except Exception as e:
        print(f"Error fetching project instructions: {e}")
    return ""


# ── ROUTES ──

@app.route("/")
def home():
    if 'guest_messages' not in session:
        session['guest_messages'] = 0
    session["history"] = []
    session.pop('chat_id', None)
    return render_template("index.html")


@app.route('/login-page')
def login_page():
    return render_template('login.html')


# ─── PROJECT ROUTES ───

@app.route("/projects", methods=["GET"])
@login_required
def list_projects():
    """Get all projects for current user."""
    admin = get_admin_client()
    user_id = session['user_id']
    res = admin.table('projects')\
        .select('id, name, icon, custom_instructions, stream, created_at, updated_at')\
        .eq('user_id', user_id)\
        .order('updated_at', desc=True)\
        .execute()

    projects = res.data or []

    # Add chat count to each project
    for p in projects:
        count_res = admin.table('chats')\
            .select('id', count='exact')\
            .eq('project_id', p['id'])\
            .eq('user_id', user_id)\
            .execute()
        p['chat_count'] = count_res.count or 0

    return jsonify({'projects': projects})


@app.route("/projects", methods=["POST"])
@login_required
def create_project():
    """Create a new project."""
    data = request.json or {}
    name = (data.get('name') or '').strip()
    icon = (data.get('icon') or '📁').strip()[:4]
    instructions = (data.get('custom_instructions') or '').strip()
    stream = (data.get('stream') or '').strip()
    if stream not in ('science', 'commerce', 'arts', ''):
        stream = ''

    if not name:
        return jsonify({'error': 'Project name required'}), 400
    if len(name) > 50:
        return jsonify({'error': 'Project name too long (max 50 chars)'}), 400
    if len(instructions) > MAX_INSTRUCTIONS_LENGTH:
        return jsonify({'error': f'Instructions too long (max {MAX_INSTRUCTIONS_LENGTH} chars)'}), 400

    admin = get_admin_client()
    user_id = session['user_id']

    insert_data = {
        'user_id': user_id,
        'name': name,
        'icon': icon,
        'custom_instructions': instructions,
    }
    if stream:
        insert_data['stream'] = stream

    res = admin.table('projects').insert(insert_data).execute()

    if res.data:
        project = res.data[0]
        project['chat_count'] = 0
        return jsonify({'project': project})

    return jsonify({'error': 'Failed to create project'}), 500


@app.route("/projects/<project_id>", methods=["GET"])
@login_required
def get_project(project_id):
    """Get a single project with its chats."""
    admin = get_admin_client()
    user_id = session['user_id']

    proj_res = admin.table('projects')\
        .select('*')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    if not proj_res.data:
        return jsonify({'error': 'Project not found'}), 404

    project = proj_res.data[0]

    chats_res = admin.table('chats')\
        .select('id, title, updated_at')\
        .eq('project_id', project_id)\
        .eq('user_id', user_id)\
        .order('updated_at', desc=True)\
        .execute()

    project['chats'] = chats_res.data or []
    return jsonify({'project': project})


@app.route("/projects/<project_id>", methods=["PUT"])
@login_required
def update_project(project_id):
    """Update project name, icon, or custom instructions."""
    data = request.json or {}
    admin = get_admin_client()
    user_id = session['user_id']

    check = admin.table('projects').select('id').eq('id', project_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'error': 'Project not found'}), 404

    update = {'updated_at': datetime.now(timezone.utc).isoformat()}

    if 'name' in data:
        name = (data['name'] or '').strip()
        if not name:
            return jsonify({'error': 'Name cannot be empty'}), 400
        if len(name) > 50:
            return jsonify({'error': 'Name too long (max 50 chars)'}), 400
        update['name'] = name

    if 'icon' in data:
        update['icon'] = (data['icon'] or '📁').strip()[:4]

    if 'custom_instructions' in data:
        instructions = (data['custom_instructions'] or '').strip()
        if len(instructions) > MAX_INSTRUCTIONS_LENGTH:
            return jsonify({'error': f'Instructions too long (max {MAX_INSTRUCTIONS_LENGTH} chars)'}), 400
        update['custom_instructions'] = instructions

    if 'stream' in data:
        stream = (data['stream'] or '').strip()
        update['stream'] = stream if stream in ('science', 'commerce', 'arts') else None

    admin.table('projects').update(update).eq('id', project_id).execute()
    return jsonify({'success': True})


@app.route("/projects/<project_id>", methods=["DELETE"])
@login_required
def delete_project(project_id):
    """Delete a project. Chats inside become unassigned (project_id = null)."""
    admin = get_admin_client()
    user_id = session['user_id']

    check = admin.table('projects').select('id').eq('id', project_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'error': 'Project not found'}), 404

    admin.table('chats').update({'project_id': None}).eq('project_id', project_id).execute()
    admin.table('projects').delete().eq('id', project_id).execute()

    return jsonify({'success': True})


# ─── CHAT ROUTES ───

@app.route("/chats", methods=["GET"])
@login_required
def get_chats():
    """List user's chats. Optional ?project_id=X to filter by project, or ?project_id=none for unassigned."""
    admin = get_admin_client()
    user_id = session['user_id']

    project_filter = request.args.get('project_id')

    query = admin.table('chats')\
        .select('id, title, updated_at, project_id')\
        .eq('user_id', user_id)\
        .order('updated_at', desc=True)\
        .limit(50)

    if project_filter == 'none':
        query = query.is_('project_id', 'null')
    elif project_filter:
        query = query.eq('project_id', project_filter)

    res = query.execute()
    return jsonify({'chats': res.data})


@app.route("/chats/<chat_id>", methods=["GET"])
@login_required
def get_chat(chat_id):
    admin = get_admin_client()
    user_id = session['user_id']
    res = admin.table('chats').select('*').eq('id', chat_id).eq('user_id', user_id).execute()
    if not res.data:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify({'chat': res.data[0]})


@app.route("/chats/<chat_id>", methods=["DELETE"])
@login_required
def delete_chat(chat_id):
    admin = get_admin_client()
    user_id = session['user_id']
    admin.table('chats').delete().eq('id', chat_id).eq('user_id', user_id).execute()
    return jsonify({'success': True})



# ─── MEMORY ROUTES ───

@app.route("/save-quiz-result", methods=["POST"])
@login_required
def save_quiz_result_route():
    data = request.get_json() or {}
    score = data.get('score')
    total = data.get('total')
    if not isinstance(score, int) or not isinstance(total, int) or total <= 0:
        return jsonify({'ok': False}), 400
    user_id = session['user_id']
    from memory import save_quiz_result
    threading.Thread(target=save_quiz_result, args=(user_id, score, total), daemon=True).start()
    return jsonify({'ok': True})


@app.route("/student-opening", methods=["GET"])
@login_required
def student_opening():
    """Return Dipti's personalised opening line, streak, and weak topics for a new chat session."""
    from memory import get_student_profile, get_dipti_opening, save_last_stream, get_or_init_streak
    user_id = session['user_id']
    student_name = session.get('preferred_name') or session.get('name', '')
    current_stream = request.args.get('stream', '')
    profile = get_student_profile(user_id)
    opening = get_dipti_opening(profile, student_name, current_stream=current_stream)
    streak = get_or_init_streak(user_id, profile)
    if current_stream:
        threading.Thread(target=save_last_stream, args=(user_id, current_stream), daemon=True).start()
    # Return weak topics filtered to the current stream so physics topics
    # don't appear in arts/commerce projects and vice versa
    all_weak = (profile.get('weak_topics') or []) if profile else []
    if current_stream and all_weak:
        from chain import STREAM_INFO, detect_subject_from_question
        allowed = set(STREAM_INFO.get(current_stream, {}).get('subjects', []))
        filtered = [
            t for t in all_weak
            if detect_subject_from_question(t, fallback=None) in allowed
        ]
        weak_topics = filtered[-2:]
    else:
        weak_topics = all_weak[-2:]
    return jsonify({'opening': opening, 'streak': streak, 'weak_topics': weak_topics})


@app.route("/save-stream", methods=["POST"])
@login_required
def save_stream():
    """Persist the student's stream selection to their profile permanently."""
    stream = request.json.get("stream", "").strip()
    if stream not in ("science", "commerce", "arts"):
        return jsonify({"error": "invalid stream"}), 400
    from memory import save_last_stream
    threading.Thread(target=save_last_stream, args=(session['user_id'], stream), daemon=True).start()
    return jsonify({"ok": True})


# ─── ASK ROUTES ───

@app.route("/ask", methods=["POST"])
def ask():
    """Non-streaming endpoint. Kept for backwards compat / fallback."""
    data = request.json
    user_message = data.get("message", "")
    chat_id = data.get("chat_id")
    project_id = data.get("project_id")
    subject = data.get("subject", "biology")  # default to biology

    if not user_message:
        return jsonify({"error": "No message"}), 400

    # Guest user
    if 'user_id' not in session:
        count = session.get('guest_messages', 0)
        if count >= 5:
            return jsonify({
                "login_required": True,
                "error": "You've used your 5 free messages. Please login to continue!"
            }), 401
        session['guest_messages'] = count + 1
        if "history" not in session:
            session["history"] = []
        recent = session["history"][-10:]
        result = get_answer(user_message, recent, project_instructions="", subject=subject)
        if isinstance(result, dict):
            reply = result.get("reply", "")
        else:
            reply = result
        session["history"].append({"role": "user", "content": user_message})
        session["history"].append({"role": "assistant", "content": reply})
        session.modified = True
        return jsonify({"reply": reply})

    # Logged-in user
    if not check_message_limit(session['user_id'], session.get('plan', 'free')):
        return jsonify({"error": "Daily free limit reached."}), 429

    user_id = session['user_id']

    project_instructions = ""
    if project_id:
        project_instructions = get_project_instructions(project_id, user_id)

    chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=subject)
    current_chat_id = chat['id']
    messages = chat.get('messages', [])

    recent = messages[-10:] if len(messages) > 10 else messages
    history = [{k: m[k] for k in ("role", "content", "image_url") if k in m} for m in recent]

    result = get_answer(user_message, history, project_instructions=project_instructions, subject=subject)

    if isinstance(result, dict):
        reply = result.get("reply", "")
        chapters_found = result.get("chapters_found", [])
    else:
        reply = result
        chapters_found = []

    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": reply})

    threading.Thread(
        target=background_save,
        args=(current_chat_id, messages, user_message, user_id),
        daemon=True
    ).start()

    return jsonify({
        "reply": reply,
        "chat_id": current_chat_id,
        "chapters_found": chapters_found
    })


@app.route("/ask-stream", methods=["POST"])
@limiter.limit("20 per minute; 300 per day")
def ask_stream():
    """
    Server-Sent Events endpoint that streams thinking stages + final reply.
    Events emitted (in order):
      - {type: "stage", text: "..."}                  (thinking message)
      - {type: "chapters", chapters: [...]}            (only for biology questions)
      - {type: "reply", reply: "...", chat_id: "..."}  (final answer)
      - {type: "error", error: "..."}
    """
    data = request.json
    user_message = data.get("message", "")
    chat_id = data.get("chat_id")
    project_id = data.get("project_id")
    subject = data.get("subject", "biology")
    stream = data.get("stream", "")

    if not user_message:
        return jsonify({"error": "No message"}), 400

    # Capture session values BEFORE entering the generator
    is_logged_in = 'user_id' in session
    user_id = session.get('user_id')
    plan = session.get('plan', 'free')
    guest_count = session.get('guest_messages', 0)
    history_session = session.get('history', [])
    # preferred_name in session wins over login-time name (persists name corrections)
    student_name = session.get('preferred_name') or session.get('name', '')

    # Detect name correction upfront from the message itself — update session immediately
    if is_logged_in and user_message:
        from chain import detect_name_correction
        _nc = detect_name_correction(user_message)
        if _nc:
            student_name = _nc
            session['preferred_name'] = _nc
            session.modified = True

    # Guest limit check
    if not is_logged_in and guest_count >= 5:
        return jsonify({
            "login_required": True,
            "error": "You've used your 5 free messages. Please login to continue!"
        }), 401

    # Logged-in limit check
    if is_logged_in and not check_message_limit(user_id, plan):
        return jsonify({"error": "Daily free limit reached."}), 429

    # Bump guest counter immediately
    if not is_logged_in:
        session['guest_messages'] = guest_count + 1
        session.modified = True

    def event_stream():
        from chain import do_rag_lookup, run_llm, is_toc_question, build_toc_response, detect_subject_in_question, detect_subject_from_question, is_casual_chat, instant_reply, check_stream_mismatch, is_roadmap_request, detect_chapter_from_message, generate_section_list, is_despair, detect_subject_for_roadmap
        from memory import get_student_profile
        student_profile = get_student_profile(user_id) if is_logged_in else None

        def sse(payload):
            return f"data: {json.dumps(payload)}\n\n"

        try:
            # 1. Check if user explicitly named a subject ("biology chapter", "physics question")
            explicit_subject = detect_subject_in_question(user_message, fallback=None)
            # 2. Try content keyword detection ("সালোকসংশ্লেষণ" → biology)
            content_subject = detect_subject_from_question(user_message, fallback=None) if not explicit_subject else None
            # 3. Resolve: explicit > content-detected > frontend activeSubject
            subject_confirmed = bool(explicit_subject or content_subject)
            effective_subject = explicit_subject or content_subject or subject

            # 4. Stream-consistency guard: if subject wasn't confirmed by detection,
            #    ensure it belongs to the current stream (prevents stale cross-stream subjects).
            _STREAM_SUBJECTS = {
                'science':  {'biology', 'physics', 'chemistry', 'math'},
                'commerce': {'accounting', 'math'},
                'arts':     {'geography', 'math'},
            }
            _STREAM_DEFAULTS = {'science': 'biology', 'commerce': 'accounting', 'arts': 'geography'}
            if not subject_confirmed and stream and stream in _STREAM_SUBJECTS:
                if effective_subject not in _STREAM_SUBJECTS[stream]:
                    effective_subject = _STREAM_DEFAULTS[stream]

            # ── ROADMAP: must run BEFORE mismatch check (no subject needed) ──
            if is_roadmap_request(user_message):
                # Use stream's primary subject if student didn't name one
                _stream_primary = {'science': 'biology', 'commerce': 'accounting', 'arts': 'geography'}
                # Fuzzy match first (catches typos like "physcis"), then explicit, then stream default
                roadmap_subject = detect_subject_for_roadmap(user_message) or explicit_subject or _stream_primary.get(stream) or content_subject or effective_subject
                chapter_info = detect_chapter_from_message(user_message, roadmap_subject)
                from rag.chapters import CHAPTERS
                chs = CHAPTERS.get(roadmap_subject, {})
                if chapter_info:
                    # Specific chapter → show section-level roadmap
                    chapter_num, chapter_title = chapter_info
                    yield sse({"type": "stage", "text": "চল, তোমার জন্য একটা দারুণ পড়ার রোডম্যাপ সাজিয়ে ফেলি... 📚🌱"})
                    sections = generate_section_list(roadmap_subject, chapter_num, chapter_title)
                    if sections:
                        yield sse({
                            "type": "roadmap",
                            "level": "chapter",
                            "subject": roadmap_subject,
                            "chapter_num": chapter_num,
                            "chapter_title": chapter_title,
                            "sections": sections,
                        })
                        return
                elif chs:
                    # No specific chapter → show full book roadmap so student can pick
                    all_chapters = [{"num": num, "title": title} for _, (num, title) in chs.items()]
                    yield sse({
                        "type": "roadmap",
                        "level": "book",
                        "subject": roadmap_subject,
                        "chapters": all_chapters,
                    })
                    return

            # ── STREAM MISMATCH: soft redirect before doing any LLM work ──
            # Skip mismatch check when subject is ambiguous + it's a TOC question —
            # those go to the clarify path below and don't need stream enforcement.
            mismatch_msg = check_stream_mismatch(stream, effective_subject) if subject_confirmed else None
            if mismatch_msg:
                current_chat_id = None
                if is_logged_in:
                    chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=effective_subject)
                    current_chat_id = chat['id']
                    messages_list = chat.get('messages', [])
                    messages_list.append({"role": "user", "content": user_message})
                    messages_list.append({"role": "assistant", "content": mismatch_msg})
                    threading.Thread(
                        target=background_save,
                        args=(current_chat_id, messages_list, user_message, user_id),
                        daemon=True
                    ).start()
                yield sse({"type": "reply", "reply": mismatch_msg, "chat_id": current_chat_id, "chapters_found": [], "chips": False})
                return

            # ── TOC QUESTIONS: BYPASS LLM ENTIRELY ──
            if is_toc_question(user_message):
                # If no subject was explicitly named or content-detected, ask rather than guess wrong
                if not subject_confirmed:
                    clarify = (
                        "তুমি কোন বিষয়ের অধ্যায়ের তালিকা দেখতে চাচ্ছ বলো তো? 😊\n\n"
                        "ঝটপট জানিয়ে দাও — **জীববিজ্ঞান**, **পদার্থবিজ্ঞান**, **রসায়ন**, **হিসাববিজ্ঞান** নাকি **ভূগোল**?"
                    )
                    current_chat_id = None
                    if is_logged_in:
                        chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=effective_subject)
                        current_chat_id = chat['id']
                        messages_list = chat.get('messages', [])
                        messages_list.append({"role": "user", "content": user_message})
                        messages_list.append({"role": "assistant", "content": clarify})
                        threading.Thread(
                            target=background_save,
                            args=(current_chat_id, messages_list, user_message, user_id),
                            daemon=True
                        ).start()
                    yield sse({"type": "reply", "reply": clarify, "chat_id": current_chat_id, "chapters_found": [], "chips": False})
                    return

                toc_reply = build_toc_response(effective_subject)
                if toc_reply:
                    yield sse({"type": "stage", "text": "Looking up chapter list..."})
                    yield sse({"type": "chapters", "chapters": ["অধ্যায় তালিকা (Table of Contents)"]})

                    # Save to chat history (logged-in users)
                    current_chat_id = None
                    if is_logged_in:
                        chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=effective_subject)
                        current_chat_id = chat['id']
                        messages_list = chat.get('messages', [])
                        messages_list.append({"role": "user", "content": user_message})
                        messages_list.append({"role": "assistant", "content": toc_reply})
                        threading.Thread(
                            target=background_save,
                            args=(current_chat_id, messages_list, user_message, user_id),
                            daemon=True
                        ).start()

                    yield sse({
                        "type": "reply",
                        "reply": toc_reply,
                        "chat_id": current_chat_id,
                        "chapters_found": ["অধ্যায় তালিকা"],
                        "chips": False
                    })
                    return  # CRITICAL: exit, don't run LLM

            # ── GUIDE SECTION: teach a specific section step-by-step ──
            if user_message.startswith('__GUIDE_SECTION__:'):
                section_name = user_message[len('__GUIDE_SECTION__:'):]
                project_instructions = ""
                current_chat_id = None
                messages_list = []
                if is_logged_in:
                    chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=effective_subject)
                    current_chat_id = chat['id']
                    messages_list = chat.get('messages', [])
                    history = [{k: m[k] for k in ("role", "content", "image_url") if k in m} for m in messages_list[-10:]]
                    if project_id:
                        project_instructions = get_project_instructions(project_id, user_id)
                nctb_context, _ = do_rag_lookup(section_name, subject=effective_subject)
                guide_query = (
                    f"এখন শুধু এই section টি পড়াও: **{section_name}**\n"
                    f"NCTB বই এর ক্রম অনুযায়ী সহজ ভাষায় explain করো। "
                    f"example দাও। শেষে বোঝার জন্য একটা ছোট প্রশ্ন করো।"
                )
                reply = run_llm(guide_query, history, nctb_context, project_instructions,
                                stream=stream, student_name=student_name,
                                subject=effective_subject, student_profile=student_profile)
                if is_logged_in:
                    messages_list.append({"role": "user", "content": section_name})
                    messages_list.append({"role": "assistant", "content": reply})
                    threading.Thread(
                        target=background_save,
                        args=(current_chat_id, messages_list, section_name, user_id),
                        daemon=True
                    ).start()
                yield sse({"type": "reply", "reply": reply, "chat_id": current_chat_id,
                           "chapters_found": [], "chips": True})
                return

            # ── QUIZ: chip trigger OR natural language ("10 ta quiz kore dayow") ──
            _is_chip = (user_message == '__QUIZ__' or user_message == '__REVIEW_QUIZ__')
            if user_message == '__REVIEW_QUIZ__':
                _quiz_total = 3
            elif not _is_chip:
                from chain import parse_quiz_request
                _quiz_total = parse_quiz_request(user_message)
            else:
                _quiz_total = 1
            if _is_chip or _quiz_total > 0:
                from chain import generate_quiz_mcq
                yield sse({"type": "stage", "text": "একটু দাঁড়াও, প্রশ্নটা রেডি করে নিচ্ছি... 🎯"})
                current_chat_id = None
                messages_list = []
                if is_logged_in:
                    chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=effective_subject)
                    current_chat_id = chat['id']
                    messages_list = chat.get('messages', [])
                    history = [{"role": m["role"], "content": m["content"]} for m in messages_list[-6:]]
                else:
                    history = history_session[-6:]
                mcq = generate_quiz_mcq(history, subject=effective_subject, user_query=user_message if not _is_chip else '')
                if not mcq:
                    yield sse({"type": "reply", "reply": "এই মুহূর্তে প্রশ্নটা রেডি করতে পারছি না রে। একটু পরে এসে আবার চেষ্টা করো তো! 🌱", "chat_id": current_chat_id, "chapters_found": [], "chips": False})
                    return
                if mcq.get("exhausted"):
                    yield sse({"type": "reply", "reply": "এই টপিকে, আপাতত আর নতুন প্রশ্ন নেই! 🎯 আরো বিষয় পড়লে আরো quiz দিতে পারবো। ঠিক আছে?", "chat_id": current_chat_id, "chapters_found": [], "chips": False})
                    return
                if mcq.get("no_topic"):
                    yield sse({"type": "reply", "reply": "কোন অধ্যায় বা বিষয় নিয়ে কুইজ দেব বলো? 🌱", "chat_id": current_chat_id, "chapters_found": [], "chips": False})
                    return
                opts = mcq['options']
                mcq_history_text = f"🎯 Quiz\n{mcq['question']}\nA) {opts['A']}\nB) {opts['B']}\nC) {opts['C']}\nD) {opts['D']}\n[সঠিক উত্তর: {mcq['correct']}]"
                display_msg = "নিজেকে Test করো 🎯" if user_message == '__REVIEW_QUIZ__' else ("Quiz করো 🎯" if _is_chip else user_message)
                if is_logged_in:
                    messages_list.append({"role": "user", "content": display_msg})
                    messages_list.append({"role": "assistant", "content": mcq_history_text})
                    threading.Thread(
                        target=background_save,
                        args=(current_chat_id, messages_list, display_msg, user_id),
                        daemon=True
                    ).start()
                yield sse({"type": "quiz", "mcq": mcq, "quiz_total": _quiz_total, "chat_id": current_chat_id})
                return

            # ── CASUAL CHAT: skip RAG and stage indicators entirely ──
            if is_casual_chat(user_message):
                current_chat_id = None
                messages_list = []
                project_instructions = ""
                # Try zero-latency hardcoded reply first
                reply = instant_reply(user_message)
                if is_logged_in:
                    if project_id:
                        project_instructions = get_project_instructions(project_id, user_id)
                    chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=effective_subject)
                    current_chat_id = chat['id']
                    messages_list = chat.get('messages', [])
                if reply is None:
                    # Needs LLM but still skip RAG
                    if is_logged_in:
                        recent = messages_list[-10:] if len(messages_list) > 10 else messages_list
                        history = [{k: m[k] for k in ("role", "content", "image_url") if k in m} for m in recent]
                    else:
                        history = history_session[-10:]
                    reply = run_llm(user_message, history, nctb_context="", project_instructions=project_instructions, stream=stream, student_name=student_name, subject=effective_subject, student_profile=student_profile)
                if is_logged_in:
                    messages_list.append({"role": "user", "content": user_message})
                    messages_list.append({"role": "assistant", "content": reply})
                    threading.Thread(
                        target=background_save,
                        args=(current_chat_id, messages_list, user_message, user_id),
                        daemon=True
                    ).start()
                yield sse({"type": "reply", "reply": reply, "chat_id": current_chat_id, "chapters_found": [], "chips": False})

                # Wrap-up: goodbye after a real session (casual path)
                if is_logged_in:
                    from memory import is_goodbye, generate_wrapup, save_session_promise
                    if is_goodbye(user_message) and len(messages_list) >= 6:
                        wrapup_text, promise = generate_wrapup(messages_list, student_name)
                        if wrapup_text:
                            yield sse({"type": "wrapup", "reply": wrapup_text})
                        if promise:
                            threading.Thread(
                                target=save_session_promise,
                                args=(user_id, promise, messages_list),
                                daemon=True
                            ).start()
                return

            # ── STAGE 1: Generic thinking placeholder ──
            yield sse({"type": "stage", "text": "Thinking..."})

            # Run RAG silently — don't tell the user we're searching unless we actually find something
            nctb_context, chapters_found = do_rag_lookup(user_message, subject=effective_subject)
            is_biology = bool(nctb_context and nctb_context.strip()) and bool(chapters_found)

            # ── STAGE 2: Only show "found in textbook" if we actually found relevant content ──
            if is_biology:
                yield sse({"type": "stage", "text": "Searching NCTB textbook..."})
                yield sse({"type": "chapters", "chapters": chapters_found})

            # ── STAGE 3: Writing answer ──
            yield sse({"type": "stage", "text": "Writing your answer..."})

            project_instructions = ""
            current_chat_id = None
            messages_list = []

            if is_logged_in:
                if project_id:
                    project_instructions = get_project_instructions(project_id, user_id)
                chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=effective_subject)
                current_chat_id = chat['id']
                messages_list = chat.get('messages', [])
                # Narrow window when subject isn't keyword-confirmed to prevent topic bleed
                recent_count = 4 if not subject_confirmed else 10
                recent = messages_list[-recent_count:] if len(messages_list) > recent_count else messages_list
                history = [
                    {k: v for k, v in m.items() if k in ("role", "content", "image_url")}
                    for m in recent
                ]
            else:
                recent_count = 4 if not subject_confirmed else 10
                history = history_session[-recent_count:]

            from chain import stream_llm
            import time as _time
            reply = ""
            _t_llm_start = _time.time()
            _first_token_time = None
            for chunk in stream_llm(user_message, history, nctb_context, project_instructions, stream=stream, student_name=student_name, subject=effective_subject, student_profile=student_profile):
                if chunk:
                    if _first_token_time is None:
                        _first_token_time = _time.time()
                    reply += chunk
                    yield sse({"type": "token", "text": chunk})
            _t_llm_end = _time.time()
            print(f"LLM: {_t_llm_end - _t_llm_start:.2f}s | first token: {(_first_token_time - _t_llm_start):.2f}s | chars: {len(reply)} (~{len(reply)//4} tokens)")

            if is_logged_in:
                messages_list.append({"role": "user", "content": user_message})
                messages_list.append({"role": "assistant", "content": reply})
                threading.Thread(
                    target=background_save,
                    args=(current_chat_id, messages_list, user_message, user_id),
                    daemon=True
                ).start()

            chips_value = "offer_roadmap" if is_despair(user_message) else True
            yield sse({
                "type": "reply",
                "reply": reply,
                "chat_id": current_chat_id,
                "chapters_found": chapters_found if is_biology else [],
                "chips": chips_value,
                "subject": effective_subject,
            })

            # ── SESSION WRAP-UP: trigger when student says goodbye after a real session ──
            if is_logged_in:
                from memory import is_goodbye, generate_wrapup, save_session_promise
                if is_goodbye(user_message) and len(messages_list) >= 6:
                    wrapup_text, promise = generate_wrapup(messages_list, student_name)
                    if wrapup_text:
                        yield sse({"type": "wrapup", "reply": wrapup_text})
                    if promise:
                        threading.Thread(
                            target=save_session_promise,
                            args=(user_id, promise, messages_list),
                            daemon=True
                        ).start()

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield sse({"type": "error", "error": str(e)})

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.route("/ask-image", methods=["POST"])
@login_required
@limiter.limit("10 per minute; 100 per day")
def ask_image():
    print("[ask-image] request received", flush=True)
    from storage import upload_image

    image_file = request.files.get("image")
    user_message = request.form.get("message", "")
    chat_id = request.form.get("chat_id")
    project_id = request.form.get("project_id")

    if not image_file:
        return jsonify({"error": "No image"}), 400

    user_id = session['user_id']

    project_instructions = ""
    if project_id:
        project_instructions = get_project_instructions(project_id, user_id)

    subject = request.form.get("subject", "biology")
    stream  = request.form.get("stream", "")

    chat = get_or_create_chat(user_id, chat_id, project_id=project_id, subject=subject)
    current_chat_id = chat['id']
    subject = chat.get('subject') or subject  # stored chat subject is authoritative
    messages = chat.get('messages', [])

    image_bytes = image_file.read()
    image_type = image_file.content_type or "image/jpeg"

    # Fix EXIF rotation; only enhance if image is actually dark (not clear screenshots)
    try:
        from PIL import Image, ImageOps, ImageEnhance, ImageStat
        import io as _io
        pil_img = ImageOps.exif_transpose(Image.open(_io.BytesIO(image_bytes)))
        # RGBA/P mode can't be saved as JPEG — convert to RGB
        if pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
        # Measure brightness — only boost dark/low-contrast exam photos, skip clear screenshots
        mean_brightness = ImageStat.Stat(pil_img.convert("L")).mean[0]
        if mean_brightness < 160:
            pil_img = ImageEnhance.Contrast(pil_img).enhance(1.3)
            pil_img = ImageEnhance.Sharpness(pil_img).enhance(1.5)
            print(f"[image-preprocess] dark image (brightness={mean_brightness:.0f}) — enhanced")
        else:
            print(f"[image-preprocess] clear image (brightness={mean_brightness:.0f}) — no enhancement")
        buf = _io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=90)
        image_bytes = buf.getvalue()
        image_type = "image/jpeg"
    except Exception as e:
        print(f"[image-preprocess] warning: {e}")  # keep original bytes on failure

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    # Upload to Supabase Storage so we can show it on chat reload
    image_url = upload_image(image_bytes, image_type, user_id)

    recent = messages[-10:] if len(messages) > 10 else messages
    history = [{"role": m["role"], "content": m["content"]} for m in recent]

    from memory import get_student_profile
    student_profile = get_student_profile(user_id)
    _img_student_name = student_profile.get('preferred_name') or session.get('name', '') if student_profile else session.get('name', '')

    try:
        answer, show_chips, detected_subject = get_answer_with_image(
            user_message, history, image_base64, image_type,
            project_instructions=project_instructions,
            subject=subject,
            stream=stream,
            student_name=_img_student_name,
            student_profile=student_profile,
        )
    except Exception as e:
        import traceback; traceback.print_exc()
        err_str = str(e).lower()
        if any(k in err_str for k in ("quota", "429", "rate", "resource_exhausted", "resourceexhausted")):
            msg = "এই মুহূর্তে সার্ভার একটু ব্যস্ত রে — ১ মিনিট পরে আবার চেষ্টা করো! ⏳"
        elif any(k in err_str for k in ("timeout", "deadline", "unavailable", "503")):
            msg = "সার্ভার সাড়া দিচ্ছে না, একটু পরে আবার চেষ্টা করো। 🔄"
        else:
            msg = "ছবিটা প্রসেস করতে সমস্যা হলো — আবার চেষ্টা করো অথবা ছবি আবার তুলে পাঠাও। 📸"
        print(f"[ask-image] classified error: {type(e).__name__}: {e}")
        return jsonify({"error": msg}), 500

    # Persist message WITH image URL — this is the fix for "image disappears on reload"
    user_msg = {
        "role": "user",
        "content": user_message or "এই ছবিটি দেখে বুঝিয়ে দাও।",
    }
    if image_url:
        user_msg["image_url"] = image_url

    messages.append(user_msg)
    messages.append({"role": "assistant", "content": answer})

    threading.Thread(
        target=lambda: (
            save_messages(current_chat_id, messages),
            auto_title_image(current_chat_id, detected_subject, answer),
            increment_message_count(user_id),
        ),
        daemon=True
    ).start()

    return jsonify({
        "reply": answer,
        "chat_id": current_chat_id,
        "image_url": image_url,
        "chips": show_chips,
        "subject": detected_subject,
    })


@app.route('/transcribe', methods=['POST'])
@login_required
def transcribe():
    audio = request.files.get('audio')
    if not audio:
        return jsonify({'error': 'No audio'}), 400
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        result = client.audio.transcriptions.create(
            model='whisper-1',
            file=('audio.webm', audio.stream, audio.content_type or 'audio/webm'),
            prompt='বাংলা ভাষা। হ্যালো দীপ্তি আপু, আমাকে জীববিজ্ঞান, পদার্থবিজ্ঞান, রসায়ন, গণিত, ভূগোল বুঝিয়ে দাও। রেচন প্রক্রিয়া, কোষ বিভাজন, সালোকসংশ্লেষণ।',
            temperature=0,
        )
        return jsonify({'text': result.text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response

if __name__ == "__main__":
    app.run(debug=True, threaded=True, use_reloader=False)