from flask import Blueprint, request, jsonify, session
from supabase import create_client
import os
import hashlib
import random
import secrets
import threading
from datetime import datetime, timedelta, timezone

auth_bp = Blueprint('auth', __name__)

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)

APP_NAME = "Sheelbi"


# ──────────────────────────────────────────
#  ADMIN CLIENT (bypasses RLS)
# ──────────────────────────────────────────

def get_admin_client():
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY")
    )


# ──────────────────────────────────────────
#  DEVICE FINGERPRINT
# ──────────────────────────────────────────

def get_device_fingerprint(req):
    user_agent = req.headers.get("User-Agent", "unknown")
    return hashlib.sha256(user_agent.encode()).hexdigest()


# ──────────────────────────────────────────
#  SESSION TOKEN
# ──────────────────────────────────────────

def generate_session_token():
    return secrets.token_hex(32)


def set_active_session(user_id, token, device_fp):
    get_admin_client().table('profiles').update({
        'active_session_token': token,
        'active_device_fp':     device_fp,
        'session_created_at':   datetime.utcnow().isoformat()
    }).eq('id', user_id).execute()


def validate_session(user_id, token):
    try:
        res = get_admin_client().table('profiles').select(
            'active_session_token, active_device_fp, session_created_at'
        ).eq('id', user_id).execute()

        if not res.data:
            return False, "Profile not found."

        row = res.data[0]
        db_token = row.get('active_session_token')

        if not db_token or db_token != token:
            return False, "logged_out_other_device"

        return True, "OK"
    except Exception as e:
        print(f"[VALIDATE SESSION ERROR] {e}")
        return False, "Session check failed."


# ──────────────────────────────────────────
#  EMAIL HELPERS
# ──────────────────────────────────────────

def generate_code():
    return str(random.randint(100000, 999999))


import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(to_email, subject, html_body):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f"Sheelbi <{os.getenv('GMAIL_USER')}>"
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(os.getenv('GMAIL_USER'), os.getenv('GMAIL_APP_PASS'))
            server.sendmail(os.getenv('GMAIL_USER'), to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False


def send_email_async(to_email, subject, html_body):
    t = threading.Thread(target=send_email, args=(to_email, subject, html_body), daemon=True)
    t.start()


def email_template(title, body_html, code=None, footer=""):
    code_block = ""
    if code:
        code_block = f"""
        <tr><td align="center" style="padding:28px 0 8px;">
          <table cellpadding="0" cellspacing="0" border="0">
            <tr><td style="background:#0e0f11;border:1.5px solid #6c7fff;
                 border-radius:14px;padding:20px 40px;text-align:center;">
              <span style="font-size:36px;font-weight:700;letter-spacing:12px;
                   color:#8a9bff;font-family:monospace;">{code}</span>
            </td></tr>
          </table>
          <p style="color:#7a8499;font-size:12px;margin-top:12px;">
            Expires in <strong style="color:#f0a855;">10 minutes</strong>
          </p>
        </td></tr>"""

    footer_text = footer if footer else "If you didn't create a Sheelbi account, you can safely ignore this email."

    return f"""
<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#ffffff;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#ffffff;padding:20px 16px;">
  <tr><td align="center">
    <table width="480" cellpadding="0" cellspacing="0" border="0"
           style="max-width:480px;width:100%;background:#16181c;
                  border-radius:18px;border:1px solid #252930;
                  font-family:'Segoe UI',Arial,sans-serif;">
      <tr><td style="background:#0e0f11;padding:28px 32px;
               text-align:center;border-radius:18px 18px 0 0;
               border-bottom:1px solid #252930;">
        <div style="margin:0 auto 14px;width:64px;height:64px;
             background:linear-gradient(135deg,#6c7fff,#f0a855);
             border-radius:16px;line-height:64px;text-align:center;
             box-shadow:0 4px 20px rgba(108,127,255,0.4);">
          <span style="font-size:30px;line-height:64px;">🎓</span>
        </div>
        <div style="color:#ffffff;font-size:22px;font-weight:700;
             letter-spacing:1px;margin:8px 0 4px;">Sheelbi</div>
        <div style="color:#7a8499;font-size:12px;">
          NCTB AI Tutor &middot; Class 1&ndash;12
        </div>
      </td></tr>
      <tr><td style="padding:32px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr><td>
            <h2 style="color:#e2e6ef;font-size:18px;font-weight:600;
                margin:0 0 10px;">{title}</h2>
            <div style="color:#7a8499;font-size:14px;line-height:1.8;">
              {body_html}
            </div>
          </td></tr>
          {code_block}
          <tr><td style="padding-top:24px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td width="3" style="background:#6c7fff;border-radius:0;"></td>
                <td style="background:#1c1f24;border:1px solid #252930;
                     border-left:none;border-radius:0 8px 8px 0;
                     padding:14px 16px;font-size:12px;
                     color:#7a8499;line-height:1.7;">
                  {footer_text}
                </td>
              </tr>
            </table>
          </td></tr>
        </table>
      </td></tr>
      <tr><td style="background:#0e0f11;padding:16px 32px;
               text-align:center;border-radius:0 0 18px 18px;
               border-top:1px solid #252930;">
        <p style="color:#4a5266;font-size:11px;margin:0;">
          &copy; 2026 Sheelbi &middot; NCTB Curriculum &middot; Class 1&ndash;12
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def save_code(email, code, code_type):
    admin = get_admin_client()
    try:
        admin.table('verification_codes').delete()\
            .eq('email', email).eq('type', code_type).execute()
    except Exception as e:
        print(f"[SAVE CODE DELETE ERROR] {e}")

    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    result = admin.table('verification_codes').insert({
        'email':      email,
        'code':       code,
        'type':       code_type,
        'used':       False,
        'expires_at': expires_at
    }).execute()
    print(f"[SAVE CODE RESULT] {result}")


def verify_code(email, code, code_type):
    ok, msg, row_id = _check_code(email, code, code_type)
    if not ok:
        return False, msg
    try:
        get_admin_client().table('verification_codes').update({'used': True}).eq('id', row_id).execute()
    except Exception as e:
        print(f"[MARK USED ERROR] {e}")
    return True, "OK"


def verify_code_peek(email, code, code_type):
    ok, msg, row_id = _check_code(email, code, code_type)
    return ok, msg, row_id


def mark_code_used(row_id):
    try:
        get_admin_client().table('verification_codes').update({'used': True}).eq('id', row_id).execute()
    except Exception as e:
        print(f"[MARK USED ERROR] {e}")


def _check_code(email, code, code_type):
    try:
        res = get_admin_client().table('verification_codes')\
            .select('*')\
            .eq('email', email)\
            .eq('code', code)\
            .eq('type', code_type)\
            .eq('used', False)\
            .execute()

        if not res.data:
            return False, "Invalid code. Please check and try again.", None

        row = res.data[0]

        expires_str = row['expires_at']
        if expires_str.endswith('Z'):
            expires_str = expires_str[:-1] + '+00:00'
        elif '+' not in expires_str and len(expires_str) <= 26:
            expires_str += '+00:00'
        expires_at = datetime.fromisoformat(expires_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        print(f"[CODE CHECK] now={now} expires={expires_at} expired={now > expires_at}")
        if now > expires_at:
            return False, "Code has expired. Please request a new one.", None

        return True, "OK", row['id']

    except Exception as e:
        print(f"[CHECK CODE ERROR] {e}")
        import traceback
        traceback.print_exc()
        return False, "Something went wrong. Please try again.", None


# ──────────────────────────────────────────
#  MESSAGE LIMIT HELPERS
# ──────────────────────────────────────────

def check_message_limit(user_id, plan):
    if plan != 'free':
        return True
    try:
        from datetime import date
        today   = str(date.today())
        admin   = get_admin_client()
        profile = admin.table('profiles').select(
            'message_count_today, last_message_date'
        ).eq('id', user_id).execute()
        if not profile.data:
            return True
        user = profile.data[0]
        if user.get('last_message_date', '') != today:
            admin.table('profiles').update({
                'message_count_today': 0,
                'last_message_date':   today
            }).eq('id', user_id).execute()
            return True
        return user.get('message_count_today', 0) < 10
    except:
        return True


def increment_message_count(user_id):
    try:
        from datetime import date
        today   = str(date.today())
        admin   = get_admin_client()
        profile = admin.table('profiles').select(
            'message_count_today, last_message_date'
        ).eq('id', user_id).execute()
        if not profile.data:
            return
        user  = profile.data[0]
        count = 1 if user.get('last_message_date', '') != today \
                  else user.get('message_count_today', 0) + 1
        admin.table('profiles').update({
            'message_count_today': count,
            'last_message_date':   today
        }).eq('id', user_id).execute()
    except:
        pass


# ──────────────────────────────────────────
#  REGISTER
# ──────────────────────────────────────────

@auth_bp.route('/register', methods=['POST'])
def register():
    data     = request.get_json()
    name     = data.get('name', '').strip()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not name or not email or not password:
        return jsonify({'error': 'Please fill in all fields.'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400

    try:
        res = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"name": name}}
        })
        if res.user is None:
            return jsonify({'error': 'Registration failed. Please try again.'}), 400

        user_id       = res.user.id
        device_fp     = get_device_fingerprint(request)
        session_token = generate_session_token()
        admin         = get_admin_client()

        # Check if profile already exists before inserting
        existing = admin.table('profiles').select('id').eq('id', user_id).execute()
        if not existing.data:
            admin.table('profiles').insert({
                'id':                   user_id,
                'name':                 name,
                'email':                email,
                'device_fingerprint':   device_fp,
                'active_session_token': session_token,
                'active_device_fp':     device_fp,
                'session_created_at':   datetime.utcnow().isoformat(),
                'message_count_today':  0,
                'plan':                 'free',
                'email_verified':       False
            }).execute()
        else:
            admin.table('profiles').update({
                'active_session_token': session_token,
                'active_device_fp':     device_fp,
                'session_created_at':   datetime.utcnow().isoformat(),
            }).eq('id', user_id).execute()

        code = generate_code()
        save_code(email, code, 'verify')

        send_email_async(
            email,
            f"Verify your email — {APP_NAME}",
            email_template(
                "Verify your email address",
                f"Hi <strong style='color:#e2e6ef'>{name}</strong>! 👋<br><br>"
                "Welcome to Sheelbi — your NCTB AI Tutor. "
                "Enter the code below to verify your email and start learning:",
                code=code
            )
        )

        session.permanent = True
        session['user_id']       = user_id
        session['name']          = name
        session['email']         = email
        session['device_fp']     = device_fp
        session['session_token'] = session_token
        session['plan']          = 'free'
        session['verified']      = False

        return jsonify({'success': True, 'name': name, 'needs_verification': True})

    except Exception as e:
        err = str(e)
        print(f"[REGISTER ERROR] {err}")
        if 'already registered' in err or 'already exists' in err:
            return jsonify({'error': 'This email is already registered.'}), 400
        return jsonify({'error': 'Registration failed. Please try again.'}), 500


# ──────────────────────────────────────────
#  LOGIN
# ──────────────────────────────────────────

@auth_bp.route('/login', methods=['POST'])
def login():
    data     = request.get_json()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'error': 'Please enter your email and password.'}), 400

    try:
        res = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        if res.user is None:
            return jsonify({'error': 'Incorrect email or password.'}), 401

        user_id       = res.user.id
        device_fp     = get_device_fingerprint(request)
        session_token = generate_session_token()
        admin         = get_admin_client()

        # ── Guard: fetch profile, auto-create with admin client if missing ──
        profile_res = admin.table('profiles').select('*').eq('id', user_id).execute()

        if not profile_res.data:
            print(f"[LOGIN] No profile found for {user_id}, auto-creating...")
            auth_name = res.user.user_metadata.get('name', email.split('@')[0])
            try:
                admin.table('profiles').insert({
                    'id':                   user_id,
                    'name':                 auth_name,
                    'email':                email,
                    'device_fingerprint':   device_fp,
                    'active_session_token': session_token,
                    'active_device_fp':     device_fp,
                    'session_created_at':   datetime.utcnow().isoformat(),
                    'message_count_today':  0,
                    'plan':                 'free',
                    'email_verified':       False
                }).execute()
                print(f"[LOGIN] Profile auto-created for {user_id}")
            except Exception as insert_err:
                print(f"[LOGIN] Auto-create failed: {insert_err}")
                return jsonify({'error': 'Account setup failed. Please contact support.'}), 500

            user_profile = {
                'name':           auth_name,
                'plan':           'free',
                'email_verified': False
            }
        else:
            user_profile = profile_res.data[0]
            # Update session on existing profile
            admin.table('profiles').update({
                'active_session_token': session_token,
                'active_device_fp':     device_fp,
                'session_created_at':   datetime.utcnow().isoformat()
            }).eq('id', user_id).execute()

        is_verified = user_profile.get('email_verified', False)

        session.permanent = True
        session['user_id']       = user_id
        session['name']          = user_profile.get('name')
        session['email']         = email
        session['device_fp']     = device_fp
        session['session_token'] = session_token
        session['plan']          = user_profile.get('plan', 'free')
        session['verified']      = is_verified

        if not is_verified:
            name = user_profile.get('name', 'there')
            code = generate_code()
            save_code(email, code, 'verify')
            send_email_async(
                email,
                f"Verify your email — {APP_NAME}",
                email_template(
                    "Verify your email address",
                    f"Hi <strong style='color:#e2e6ef'>{name}</strong>! 👋<br><br>"
                    "You still need to verify your email. "
                    "Enter the code below to complete your registration:",
                    code=code
                )
            )
            return jsonify({
                'success':            True,
                'name':               user_profile.get('name'),
                'needs_verification': True
            })

        return jsonify({
            'success':  True,
            'name':     user_profile.get('name'),
            'plan':     user_profile.get('plan', 'free'),
            'verified': True
        })

    except Exception as e:
        print(f"[LOGIN ERROR] {e}")
        import traceback
        traceback.print_exc()
        err = str(e)
        if 'Invalid login credentials' in err:
            return jsonify({'error': 'Incorrect email or password.'}), 401
        if 'already registered' in err or 'already exists' in err:
            return jsonify({'error': 'This email is already registered.'}), 400
        return jsonify({'error': 'Login failed. Please try again.'}), 500


# ──────────────────────────────────────────
#  VERIFY EMAIL CODE
# ──────────────────────────────────────────

@auth_bp.route('/verify-email', methods=['POST'])
def verify_email():
    data  = request.get_json()
    email = data.get('email', '').strip().lower()
    code  = data.get('code', '').strip()

    if not email or not code:
        return jsonify({'error': 'Email and code are required.'}), 400

    ok, msg = verify_code(email, code, 'verify')
    if not ok:
        return jsonify({'error': msg}), 400

    get_admin_client().table('profiles').update(
        {'email_verified': True}
    ).eq('email', email).execute()

    if 'user_id' in session:
        session['verified'] = True

    return jsonify({'success': True})


# ──────────────────────────────────────────
#  RESEND VERIFICATION
# ──────────────────────────────────────────

@auth_bp.route('/resend-verification', methods=['POST'])
def resend_verification():
    data  = request.get_json()
    email = data.get('email', '').strip().lower()

    if not email:
        return jsonify({'error': 'Email is required.'}), 400

    admin   = get_admin_client()
    profile = admin.table('profiles').select('name, email_verified').eq('email', email).execute()

    if not profile.data:
        return jsonify({'error': 'No account found with that email.'}), 404
    if profile.data[0].get('email_verified'):
        return jsonify({'error': 'This email is already verified.'}), 400

    name = profile.data[0].get('name', 'there')
    code = generate_code()
    save_code(email, code, 'verify')

    send_email_async(
        email,
        f"Your new verification code — {APP_NAME}",
        email_template(
            "New verification code",
            f"Hi <strong style='color:#e2e6ef'>{name}</strong>!<br><br>"
            "Here's your new verification code:",
            code=code
        )
    )
    return jsonify({'success': True})


# ──────────────────────────────────────────
#  FORGOT / RESET PASSWORD
# ──────────────────────────────────────────

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    data  = request.get_json()
    email = data.get('email', '').strip().lower()

    if not email:
        return jsonify({'error': 'Please enter your email address.'}), 400

    admin   = get_admin_client()
    profile = admin.table('profiles').select('name').eq('email', email).execute()
    if profile.data:
        name = profile.data[0].get('name', 'there')
        code = generate_code()
        save_code(email, code, 'reset')
        send_email_async(
            email,
            f"Reset your password — {APP_NAME}",
            email_template(
                "Reset your password",
                f"Hi <strong style='color:#e2e6ef'>{name}</strong>!<br><br>"
                "Use the code below to reset your Sheelbi password.",
                code=code
            )
        )

    return jsonify({'success': True, 'message': 'If that email exists, a reset code has been sent.'})


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    data         = request.get_json()
    email        = data.get('email', '').strip().lower()
    code         = data.get('code', '').strip()
    new_password = data.get('new_password', '').strip()

    print(f"[RESET PASSWORD] email={email!r}, code={code!r}, pwd_len={len(new_password)}")

    if not email or not code or not new_password:
        return jsonify({'error': 'All fields are required.'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400

    ok, msg, code_row_id = verify_code_peek(email, code, 'reset')
    if not ok:
        print(f"[RESET PASSWORD] verify_code failed: {msg}")
        return jsonify({'error': msg}), 400

    try:
        admin       = get_admin_client()
        users       = admin.auth.admin.list_users()
        target_user = next((u for u in users if u.email == email), None)
        if not target_user:
            return jsonify({'error': 'Account not found.'}), 404

        admin.auth.admin.update_user_by_id(target_user.id, {"password": new_password})
        mark_code_used(code_row_id)

        new_token = generate_session_token()
        admin.table('profiles').update({
            'active_session_token': new_token,
            'active_device_fp':     None,
            'session_created_at':   datetime.utcnow().isoformat()
        }).eq('email', email).execute()

        send_email_async(
            email,
            f"Password changed — {APP_NAME}",
            email_template(
                "Your password has been changed",
                "Your Sheelbi password was successfully reset. "
                "You can now log in with your new password.<br><br>"
                "If you didn't do this, please contact us immediately.",
                footer="This is a security notification from Sheelbi."
            )
        )

        return jsonify({'success': True})

    except Exception as e:
        print(f"[RESET PASSWORD ERROR] {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Failed to reset password. Please try again.'}), 500


# ──────────────────────────────────────────
#  LOGOUT
# ──────────────────────────────────────────

@auth_bp.route('/logout', methods=['POST'])
def logout():
    user_id = session.get('user_id')
    if user_id:
        try:
            get_admin_client().table('profiles').update({
                'active_session_token': None,
                'active_device_fp':     None
            }).eq('id', user_id).execute()
        except:
            pass
    session.clear()
    return jsonify({'success': True})


# ──────────────────────────────────────────
#  ME
# ──────────────────────────────────────────

@auth_bp.route('/me', methods=['GET'])
def me():
    if 'user_id' not in session:
        return jsonify({'logged_in': False}), 401

    user_id       = session['user_id']
    session_token = session.get('session_token')

    valid, reason = validate_session(user_id, session_token)
    if not valid:
        session.clear()
        if reason == "logged_out_other_device":
            return jsonify({
                'logged_in': False,
                'kicked':    True,
                'reason':    'Your account was logged in on another device.'
            }), 401
        return jsonify({'logged_in': False}), 401

    if not session.get('verified', False):
        return jsonify({
            'logged_in':          False,
            'needs_verification': True,
            'email':              session.get('email')
        }), 401

    return jsonify({
        'logged_in': True,
        'user_id':   user_id,
        'name':      session['name'],
        'plan':      session.get('plan', 'free'),
        'verified':  session.get('verified', False)
    })


# ──────────────────────────────────────────
#  LOGIN REQUIRED DECORATOR
# ──────────────────────────────────────────

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Please log in.', 'login_required': True}), 401

        user_id       = session['user_id']
        session_token = session.get('session_token')
        valid, reason = validate_session(user_id, session_token)

        if not valid:
            session.clear()
            if reason == "logged_out_other_device":
                return jsonify({
                    'error':          'Your account was logged in on another device.',
                    'login_required': True,
                    'kicked':         True
                }), 401
            return jsonify({'error': 'Session expired. Please log in again.', 'login_required': True}), 401

        return f(*args, **kwargs)
    return decorated