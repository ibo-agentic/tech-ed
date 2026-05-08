from flask import Flask, render_template, request, jsonify, session, Response, stream_with_context
from dotenv import load_dotenv
from chain import get_answer
from auth import auth_bp, login_required, check_message_limit, increment_message_count, get_admin_client
from image_chain import get_answer_with_image
import base64
import os
import threading
from datetime import datetime, timedelta

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


# ── CONSTANTS ──
MAX_INSTRUCTIONS_LENGTH = 1000


# ── CHAT HELPERS ──

def get_or_create_chat(user_id, chat_id=None, project_id=None):
    admin = get_admin_client()

    if chat_id:
        res = admin.table('chats').select('*').eq('id', chat_id).eq('user_id', user_id).execute()
        if res.data:
            return res.data[0]

    new_chat_data = {
        'user_id': user_id,
        'title': 'New Chat',
        'messages': [],
        'created_at': datetime.utcnow().isoformat()
    }
    if project_id:
        new_chat_data['project_id'] = project_id

    new_chat = admin.table('chats').insert(new_chat_data).execute()
    return new_chat.data[0]


def save_messages(chat_id, messages):
    admin = get_admin_client()
    admin.table('chats').update({
        'messages': messages,
        'updated_at': datetime.utcnow().isoformat()
    }).eq('id', chat_id).execute()


def auto_title(chat_id, user_message, answer):
    try:
        admin = get_admin_client()
        title = user_message[:50].strip()
        if len(user_message) > 50:
            title += '...'
        admin.table('chats').update({'title': title}).eq('id', chat_id).execute()
    except Exception:
        pass


def background_save(chat_id, messages, user_message, answer, user_id, is_first_exchange):
    try:
        save_messages(chat_id, messages)
        if is_first_exchange:
            auto_title(chat_id, user_message, answer)
        increment_message_count(user_id)
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
        .select('id, name, icon, custom_instructions, created_at, updated_at')\
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
    icon = (data.get('icon') or '📁').strip()[:4]  # Limit icon to 4 chars (emoji)
    instructions = (data.get('custom_instructions') or '').strip()

    if not name:
        return jsonify({'error': 'Project name required'}), 400
    if len(name) > 50:
        return jsonify({'error': 'Project name too long (max 50 chars)'}), 400
    if len(instructions) > MAX_INSTRUCTIONS_LENGTH:
        return jsonify({'error': f'Instructions too long (max {MAX_INSTRUCTIONS_LENGTH} chars)'}), 400

    admin = get_admin_client()
    user_id = session['user_id']

    res = admin.table('projects').insert({
        'user_id': user_id,
        'name': name,
        'icon': icon,
        'custom_instructions': instructions,
    }).execute()

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

    # Verify ownership
    check = admin.table('projects').select('id').eq('id', project_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'error': 'Project not found'}), 404

    update = {'updated_at': datetime.utcnow().isoformat()}

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

    admin.table('projects').update(update).eq('id', project_id).execute()
    return jsonify({'success': True})


@app.route("/projects/<project_id>", methods=["DELETE"])
@login_required
def delete_project(project_id):
    """Delete a project. Chats inside become unassigned (project_id = null)."""
    admin = get_admin_client()
    user_id = session['user_id']

    # Verify ownership
    check = admin.table('projects').select('id').eq('id', project_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'error': 'Project not found'}), 404

    # Unassign chats
    admin.table('chats').update({'project_id': None}).eq('project_id', project_id).execute()
    # Delete project
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


# ─── ASK ROUTES (Modified to support projects) ───

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    user_message = data.get("message", "")
    chat_id = data.get("chat_id")
    project_id = data.get("project_id")  # Optional — if user is inside a project

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
        # Guests don't have projects
        answer = get_answer(user_message, recent, project_instructions="")
        session["history"].append({"role": "user", "content": user_message})
        session["history"].append({"role": "assistant", "content": answer})
        session.modified = True
        return jsonify({"reply": answer})

    # Logged-in user
    if not check_message_limit(session['user_id'], session.get('plan', 'free')):
        return jsonify({"error": "Daily free limit reached."}), 429

    user_id = session['user_id']

    # Get project context if applicable
    project_instructions = ""
    if project_id:
        project_instructions = get_project_instructions(project_id, user_id)

    # Get or create chat (with project assignment if new chat)
    chat = get_or_create_chat(user_id, chat_id, project_id=project_id)
    current_chat_id = chat['id']
    messages = chat.get('messages', [])

    recent = messages[-10:] if len(messages) > 10 else messages
    history = [{"role": m["role"], "content": m["content"]} for m in recent]

    # Pass project instructions to chain
    answer = get_answer(user_message, history, project_instructions=project_instructions)

    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": answer})

    is_first_exchange = len(messages) == 2

    threading.Thread(
        target=background_save,
        args=(current_chat_id, messages, user_message, answer, user_id, is_first_exchange),
        daemon=True
    ).start()

    return jsonify({"reply": answer, "chat_id": current_chat_id})


@app.route("/ask-image", methods=["POST"])
@login_required
def ask_image():
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

    chat = get_or_create_chat(user_id, chat_id, project_id=project_id)
    current_chat_id = chat['id']
    messages = chat.get('messages', [])

    image_bytes = image_file.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    image_type = image_file.content_type

    recent = messages[-10:] if len(messages) > 10 else messages
    history = [{"role": m["role"], "content": m["content"]} for m in recent]

    answer = get_answer_with_image(user_message, history, image_base64, image_type, project_instructions=project_instructions)

    messages.append({"role": "user", "content": f"[ছবি] {user_message}"})
    messages.append({"role": "assistant", "content": answer})

    is_first_exchange = len(messages) == 2

    threading.Thread(
        target=background_save,
        args=(current_chat_id, messages, user_message, answer, user_id, is_first_exchange),
        daemon=True
    ).start()

    return jsonify({"reply": answer, "chat_id": current_chat_id})


if __name__ == "__main__":
    app.run(debug=True, threaded=True, use_reloader=False)