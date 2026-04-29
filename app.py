from flask import Flask, render_template, request, jsonify, session
from dotenv import load_dotenv
from chain import get_answer
from auth import auth_bp, login_required, check_message_limit, increment_message_count
from image_chain import get_answer_with_image
from supabase import create_client
import base64
import os
import uuid

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "bangla-edtech_2026")
from datetime import timedelta
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)
app.register_blueprint(auth_bp)

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

# ── CHAT HELPERS ──

def get_or_create_chat(user_id, chat_id=None):
    if chat_id:
        res = supabase.table('chats').select('*').eq('id', chat_id).eq('user_id', user_id).execute()
        if res.data:
            return res.data[0]
    # Create new chat
    new_chat = supabase.table('chats').insert({
        'user_id': user_id,
        'title': 'New Chat',
        'messages': []
    }).execute()
    return new_chat.data[0]

def save_messages(chat_id, messages):
    supabase.table('chats').update({
        'messages': messages,
        'updated_at': 'now()'
    }).eq('id', chat_id).execute()

def auto_title(chat_id, user_message, answer):
    try:
        title = user_message[:50].strip()
        if len(user_message) > 50:
            title += '...'
        supabase.table('chats').update({'title': title}).eq('id', chat_id).execute()
    except:
        pass

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

@app.route("/chats", methods=["GET"])
@login_required
def get_chats():
    user_id = session['user_id']
    res = supabase.table('chats')\
        .select('id, title, updated_at')\
        .eq('user_id', user_id)\
        .order('updated_at', desc=True)\
        .limit(30)\
        .execute()
    return jsonify({'chats': res.data})

@app.route("/chats/<chat_id>", methods=["GET"])
@login_required
def get_chat(chat_id):
    user_id = session['user_id']
    res = supabase.table('chats').select('*').eq('id', chat_id).eq('user_id', user_id).execute()
    if not res.data:
        return jsonify({'error': 'Chat not found'}), 404
    return jsonify({'chat': res.data[0]})

@app.route("/chats/<chat_id>", methods=["DELETE"])
@login_required
def delete_chat(chat_id):
    user_id = session['user_id']
    supabase.table('chats').delete().eq('id', chat_id).eq('user_id', user_id).execute()
    return jsonify({'success': True})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    user_message = data.get("message", "")
    chat_id = data.get("chat_id")

    if not user_message:
        return jsonify({"error": "No message"}), 400

    # Guest user
    if 'user_id' not in session:
        count = session.get('guest_messages', 0)
        if count >= 5:
            return jsonify({"login_required": True,
                "error": "You've used your 5 free messages. Please login to continue!"}), 401
        session['guest_messages'] = count + 1
        if "history" not in session:
            session["history"] = []
        answer = get_answer(user_message, session["history"])
        session["history"].append({"role": "user", "content": user_message})
        session["history"].append({"role": "assistant", "content": answer})
        session.modified = True
        return jsonify({"reply": answer})

    # Logged in user
    if not check_message_limit(session['user_id'], session.get('plan', 'free')):
        return jsonify({"error": "Daily free limit reached."}), 429

    user_id = session['user_id']
    chat = get_or_create_chat(user_id, chat_id)
    current_chat_id = chat['id']
    messages = chat.get('messages', [])

    history = [{"role": m["role"], "content": m["content"]} for m in messages]
    answer = get_answer(user_message, history)

    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": answer})
    save_messages(current_chat_id, messages)

    # Auto-title after first exchange
    if len(messages) == 2:
        auto_title(current_chat_id, user_message, answer)

    increment_message_count(user_id)
    return jsonify({"reply": answer, "chat_id": current_chat_id})

@app.route("/ask-image", methods=["POST"])
@login_required
def ask_image():
    image_file = request.files.get("image")
    user_message = request.form.get("message", "")
    chat_id = request.form.get("chat_id")

    if not image_file:
        return jsonify({"error": "No image"}), 400

    user_id = session['user_id']
    chat = get_or_create_chat(user_id, chat_id)
    current_chat_id = chat['id']
    messages = chat.get('messages', [])

    image_bytes = image_file.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    image_type = image_file.content_type

    history = [{"role": m["role"], "content": m["content"]} for m in messages]
    answer = get_answer_with_image(user_message, history, image_base64, image_type)

    messages.append({"role": "user", "content": f"[ছবি] {user_message}"})
    messages.append({"role": "assistant", "content": answer})
    save_messages(current_chat_id, messages)

    if len(messages) == 2:
        auto_title(current_chat_id, user_message, answer)

    increment_message_count(user_id)
    return jsonify({"reply": answer, "chat_id": current_chat_id})

if __name__ == "__main__":
    app.run(debug=True)