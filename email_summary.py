import os
import resend
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY", "")
_FROM = os.getenv("EMAIL_FROM", "Dipti AI <noreply@diptiai.com>")

_BN_MONTHS = ['জানুয়ারি','ফেব্রুয়ারি','মার্চ','এপ্রিল','মে','জুন','জুলাই','আগস্ট','সেপ্টেম্বর','অক্টোবর','নভেম্বর','ডিসেম্বর']
_BN_DIGITS = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')


def _bn_date() -> str:
    now = datetime.now(timezone(timedelta(hours=6)))
    return f"{str(now.day).translate(_BN_DIGITS)} {_BN_MONTHS[now.month-1]}"


def _today_bdt() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=6)).strftime('%Y-%m-%d')


def _days_missed(profile: dict) -> int:
    today = _today_bdt()
    last  = (profile.get('last_session_date') or '')[:10]
    if not last:
        return 999
    try:
        return max(0, (datetime.strptime(today, '%Y-%m-%d') - datetime.strptime(last, '%Y-%m-%d')).days)
    except Exception:
        return 999


def _generate_message(profile: dict, name: str, days_missed: int) -> str:
    from chain import flash_llm, _transliterate_name_to_bangla
    from langchain_core.messages import HumanMessage
    from langchain_core.output_parsers import StrOutputParser

    first       = (name or "").strip().split()[0] if name else ""
    bangla_name = _transliterate_name_to_bangla(first) if first else "তুমি"
    last_topic  = profile.get('last_session_topic') or ''
    streak      = profile.get('streak_days') or 1
    streak_bn   = str(streak).translate(_BN_DIGITS)
    quiz_score  = profile.get('last_quiz_score')
    quiz_total  = profile.get('last_quiz_total')
    quiz_ref    = f"{quiz_score}/{quiz_total}" if quiz_score is not None and quiz_total else ''
    weak        = (profile.get('weak_topics') or [])[-2:]
    days_bn     = str(days_missed).translate(_BN_DIGITS)

    _singular = "গুরুত্বপূর্ণ: সবসময় 'তুমি' ব্যবহার করো, কখনো 'তোমরা' নয়। একজন ছাত্র।"

    if days_missed == 0:
        prompt = (
            f"তুমি দীপ্তি আপু। এই স্টাইলে email লেখো — উষ্ণ, আপন, natural বাংলা:\n"
            f"উদাহরণ: 'আজকে nucleus নিয়ে বেশ ভালো পড়লে! Quiz-এও দারুণ করেছ। "
            f"একটু weak থাকলেও সমস্যা নেই, রিভিশন দিলেই ঠিক হয়ে যাবে। কাল আবার দেখা হবে! 🌱'\n\n"
            f"ছাত্রের নাম: {bangla_name} | topic: {last_topic or 'আজকের পড়া'} | "
            f"quiz: {quiz_ref or 'নেই'} | streak: {streak_bn} দিন | "
            f"দুর্বল: {', '.join(weak) if weak else 'নেই'}\n\n"
            f"২-৩ বাক্য। কোনো signature না। {_singular}"
        )
        fallback = f"আজকেও পড়েছ — এটাই সবচেয়ে বড় কথা! 🌱 {streak_bn} দিনের streak চলছে, এটা ধরে রেখো।"

    elif days_missed <= 2:
        prompt = (
            f"তুমি দীপ্তি আপু। ছাত্র ১-২ দিন আসেনি। এই স্টাইলে লেখো:\n"
            f"উদাহরণ: 'গতকাল তোমাকে দেখলাম না! সব ঠিক আছে তো? আমরা কিন্তু বেশ ইম্পর্ট্যান্ট "
            f"একটা টপিকের মাঝখানে ছিলাম। সময় পেলে চলে এসো, আবার শুরু করবো! 🌱'\n\n"
            f"ছাত্রের নাম: {bangla_name} | শেষ topic: {last_topic or 'পড়াশোনা'}\n\n"
            f"২ বাক্য। Guilt নয়, শুধু উষ্ণতা। কোনো signature না। {_singular}"
        )
        fallback = (
            f"গতকাল তোমাকে দেখলাম না! সব ঠিক আছে তো? "
            f"আমরা কিন্তু {last_topic or 'পড়াশোনার'} মাঝখানে ছিলাম — সময় পেলে চলে এসো! 🌱"
        )

    elif days_missed < 7:
        prompt = (
            f"তুমি দীপ্তি আপু। ছাত্র {days_missed} দিন আসেনি। এই স্টাইলে লেখো:\n"
            f"উদাহরণ: 'গত ৩ দিন ধরে তোমার কোনো খোঁজ নেই! পড়াশোনায় কি একটু গ্যাপ পড়ে গেলো? "
            f"কোনো সমস্যা নেই, ব্রেক নেওয়াটাও দরকার। যখনই ফ্রি হবে আমাকে জানিও, "
            f"আমি তোমার পড়া রেডি করে রেখেছি! 🚀'\n\n"
            f"ছাত্রের নাম: {bangla_name} | {days_bn} দিন হয়ে গেছে | শেষ topic: {last_topic or 'অজানা'}\n\n"
            f"২ বাক্য। Pressure নয়, উৎসাহ। কোনো signature না। {_singular}"
        )
        fallback = (
            f"গত {days_bn} দিন ধরে তোমার কোনো খোঁজ নেই! কোনো সমস্যা নেই — "
            f"যখনই ফ্রি হবে, আমি তোমার পড়া রেডি করে রেখেছি। 🚀"
        )

    else:
        prompt = (
            f"তুমি দীপ্তি আপু। ছাত্র এক সপ্তাহ+ আসেনি। এই স্টাইলে লেখো:\n"
            f"উদাহরণ: 'এক সপ্তাহ হয়ে গেলো আমাদের দেখা নেই! সামনের পরীক্ষার জন্য কিন্তু "
            f"প্রিপারেশনটা ধরে রাখা জরুরি। চলো, আজকে মাত্র ১০ মিনিট দিয়ে শুরু করি? 🎯'\n\n"
            f"ছাত্রের নাম: {bangla_name} | {days_bn} দিন হয়ে গেছে\n\n"
            f"২ বাক্য। Exam urgency আছে কিন্তু ভয় নয়। কোনো signature না। {_singular}"
        )
        fallback = (
            f"এক সপ্তাহ হয়ে গেলো আমাদের দেখা নেই! সামনের পরীক্ষার জন্য "
            f"প্রিপারেশন ধরে রাখা জরুরি — আজকে মাত্র ১০ মিনিট দিয়ে শুরু করি? 🎯"
        )

    try:
        return (flash_llm | StrOutputParser()).invoke([HumanMessage(content=prompt)]).strip()
    except Exception:
        return fallback


def _generate_tomorrow(profile: dict, days_missed: int) -> str:
    from chain import flash_llm
    from langchain_core.messages import HumanMessage
    from langchain_core.output_parsers import StrOutputParser

    promise = profile.get('next_session_promise') or ''
    weak    = (profile.get('weak_topics') or [])[-1:]

    _singular = "সবসময় 'তুমি' ব্যবহার করো, 'তোমরা' নয়।"

    if days_missed == 0:
        prompt = (
            f"তুমি দীপ্তি আপু। 'আগামীকালের প্ল্যান' section-এর জন্য ১টি বাক্য লেখো। "
            f"প্রতিশ্রুতি: {promise or 'নেই'}। দুর্বল topic: {weak[0] if weak else 'নেই'}। "
            f"Natural বাংলা, উৎসাহী। শুধু বাক্য। {_singular}"
        )
        fallback = promise or "কাল আবার দেখা হবে — রেডি থেকো! 🌱"
    else:
        prompt = (
            f"তুমি দীপ্তি আপু। ছাত্র কিছুদিন আসেনি। কাল ফিরে আসার জন্য ১ বাক্যে motivate করো। "
            f"দুর্বল topic: {weak[0] if weak else 'নেই'}। Natural বাংলা। শুধু বাক্য। {_singular}"
        )
        fallback = "কাল একটু সময় বের করো — দীপ্তি আপু অপেক্ষায় থাকবে! 🌟"

    try:
        return (flash_llm | StrOutputParser()).invoke([HumanMessage(content=prompt)]).strip()
    except Exception:
        return fallback


def _build_html(name: str, message: str, profile: dict, days_missed: int) -> str:
    weak       = (profile.get('weak_topics') or [])[-3:]
    streak     = profile.get('streak_days') or 1
    last_topic = profile.get('last_session_topic') or ''
    topics_n   = len(profile.get('topic_schedule') or [])
    quiz_score = profile.get('last_quiz_score')
    quiz_total = profile.get('last_quiz_total')
    tomorrow   = _generate_tomorrow(profile, days_missed)
    date_str   = _bn_date()

    streak_bn = str(streak).translate(_BN_DIGITS)
    topics_bn = str(topics_n).translate(_BN_DIGITS) + 'টি' if topics_n else '—'
    quiz_stat = (
        f"{str(quiz_score).translate(_BN_DIGITS)}/{str(quiz_total).translate(_BN_DIGITS)}"
        if quiz_score is not None and quiz_total else '—'
    )

    weak_items = ''.join(
        f'<li><span class="dot"></span>{t}</li>' for t in weak
    ) if weak else '<li><span class="dot" style="background:#2f9e7e;"></span>কোনো দুর্বল topic নেই 🎉</li>'

    if days_missed == 0:
        header_sub    = f"আজকের পড়াশোনার আপডেট · {date_str}"
        learned_title = "✅ আজ যা যা শিখলে"
        learned_text  = last_topic if last_topic else 'আজকের পড়া profile-এ save হয়নি।'
        cta_text      = "দীপ্তি আপুর সাথে পড়া শুরু করো 🚀"
    elif days_missed <= 2:
        header_sub    = f"তোমাকে মিস করলাম · {date_str}"
        learned_title = "📖 শেষবার যা শিখেছিলে"
        learned_text  = last_topic if last_topic else 'এখনো কোনো topic save হয়নি।'
        cta_text      = "এখনই ফিরে এসো 🌱"
    elif days_missed < 7:
        header_sub    = f"কোথায় আছো তুমি? · {date_str}"
        learned_title = "📖 শেষবার যা শিখেছিলে"
        learned_text  = last_topic if last_topic else 'এখনো কোনো topic save হয়নি।'
        cta_text      = "আজই ফিরে এসো 🚀"
    else:
        header_sub    = f"অনেকদিন দেখা নেই · {date_str}"
        learned_title = "📖 শেষবার যা শিখেছিলে"
        learned_text  = last_topic if last_topic else 'এখনো কোনো topic save হয়নি।'
        cta_text      = "মাত্র ১০ মিনিট দিয়ে শুরু করি? 🎯"

    return f"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Dipti — Daily Recap</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Mina:wght@400;700&family=Hind+Siliguri:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  body{{margin:0;background:#efe6da;font-family:'Hind Siliguri',sans-serif;padding:30px 14px}}
  .mina{{font-family:'Mina',sans-serif}}
  .email{{max-width:480px;margin:0 auto;background:#fffdfa;border-radius:22px;overflow:hidden;box-shadow:0 20px 50px -20px rgba(120,70,40,.4);border:1px solid #f0ddca}}
  .head{{background:linear-gradient(135deg,#e8643c,#f0a35e);padding:26px 28px;color:#fff}}
  .head .brand{{display:flex;align-items:center;gap:10px}}
  .head .mark{{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.95);display:grid;place-items:center;font-family:'Mina';font-weight:700;font-size:1.2rem;color:#e8643c;flex-shrink:0}}
  .head .brand b{{font-family:'Mina';font-size:1.4rem}}
  .head .sub{{margin-top:8px;font-size:.92rem;opacity:.92}}
  .body{{padding:26px 28px}}
  .note{{background:#fde4d6;border-radius:14px;padding:16px 18px;color:#5f3a2a;font-size:.98rem;line-height:1.7}}
  .stats{{display:flex;gap:12px;margin:20px 0}}
  .stat{{flex:1;background:#fff;border:1px solid #f0ddca;border-radius:14px;padding:14px;text-align:center}}
  .stat .n{{font-family:'Mina';font-size:1.5rem;font-weight:700;color:#e8643c}}
  .stat .l{{font-size:.78rem;color:#7a6f63;margin-top:2px}}
  .sec-title{{font-family:'Mina';font-size:1.05rem;color:#2a2018;margin:22px 0 10px;display:flex;align-items:center;gap:7px}}
  .learned{{background:#dcefe6;border-left:3px solid #2f9e7e;border-radius:0 12px 12px 0;padding:13px 16px;color:#1f5e48;font-size:.95rem}}
  .weak{{list-style:none;padding:0;margin:0}}
  .weak li{{background:#fff;border:1px solid #f0ddca;border-radius:10px;padding:11px 14px;margin-bottom:8px;font-size:.93rem;color:#5f5448;display:flex;align-items:center;gap:9px}}
  .weak li .dot{{width:8px;height:8px;border-radius:50%;background:#e8a23c;flex-shrink:0}}
  .tom{{background:#fff7f0;border:1px dashed #e8643c;border-radius:12px;padding:14px 16px;font-size:.92rem;color:#7a4a30;margin-top:6px}}
  .cta{{display:block;text-align:center;background:#e8643c;color:#fff;text-decoration:none;font-family:'Mina';font-weight:700;font-size:1.05rem;padding:15px;border-radius:30px;margin-top:24px;box-shadow:0 12px 26px -10px rgba(232,100,60,.6)}}
  .foot{{text-align:center;padding:18px;font-size:.78rem;color:#9a8d7e;background:#faf2e8}}
  .foot a{{color:#e8643c;text-decoration:none}}
</style>
</head>
<body>
  <div class="email">
    <div class="head">
      <div class="brand">
        <span class="mark">দী</span>
        <b>Dipti</b>
      </div>
      <div class="sub">{header_sub}</div>
    </div>
    <div class="body">
      <div class="note">{message}</div>
      <div class="stats">
        <div class="stat"><div class="n">{topics_bn}</div><div class="l">topic শিখেছো</div></div>
        <div class="stat"><div class="n">🔥 {streak_bn}</div><div class="l">দিনের streak</div></div>
        <div class="stat"><div class="n">{quiz_stat}</div><div class="l">quiz score</div></div>
      </div>
      <div class="sec-title">{learned_title}</div>
      <div class="learned">{learned_text}</div>
      <div class="sec-title">📌 যেসব topic রিভাইজ করা প্রয়োজন</div>
      <ul class="weak">{weak_items}</ul>
      <div class="sec-title">📅 আগামীকালের প্ল্যান</div>
      <div class="tom">{tomorrow}</div>
      <a href="https://diptiai.com" class="cta">{cta_text}</a>
    </div>
    <div class="foot">
      <span class="mina" style="color:#e8643c;font-size:1rem">Dipti</span> · diptiai.com<br>
      <a href="#">আনসাবস্ক্রাইব</a> · email বন্ধ করতে app-এ Settings → Email Summary বন্ধ করো
    </div>
  </div>
</body>
</html>"""


def send_student_summary(email: str, name: str, profile: dict) -> bool:
    try:
        missed = _days_missed(profile)
        first  = (name or "").strip().split()[0] or "তুমি"

        message = _generate_message(profile, name, missed)
        html    = _build_html(name, message, profile, missed)

        if missed == 0:
            subject = f"📚 আজকের summary — {first}"
        elif missed <= 2:
            subject = f"🌱 তোমাকে মিস করলাম, {first}!"
        elif missed < 7:
            subject = f"🚀 ফিরে এসো {first} — পড়া রেডি আছে!"
        else:
            subject = f"🎯 মাত্র ১০ মিনিট, {first} — শুরু করো আজই"

        params: resend.Emails.SendParams = {
            "from": _FROM,
            "to":   [email],
            "subject": subject,
            "html": html,
        }
        resend.Emails.send(params)
        tag = ["recap", "1-2d", "3-6d", "7d+"][min(missed, 3) if missed > 0 else 0]
        print(f"[email] Sent {tag} to {email} (missed={missed}d)")
        return True
    except Exception as e:
        print(f"[email] Failed for {email}: {e}")
        return False


def run_daily_summaries():
    """Fetch all opted-in students active in last 7 days and send summaries."""
    print("[email] Running daily summaries…")
    try:
        from auth import get_admin_client
        admin  = get_admin_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        res = admin.table('student_profiles').select(
            'user_id,preferred_name,last_session_date,weak_topics,streak_days,'
            'last_session_topic,last_quiz_score,last_quiz_total,session_count,'
            'email_summary,topic_schedule,next_session_promise'
        ).execute()

        profiles = [
            p for p in (res.data or [])
            if p.get('email_summary', True) is not False
            and (p.get('last_session_date') or '') >= cutoff
        ]
        print(f"[email] {len(profiles)} students to notify")

        for profile in profiles:
            try:
                uid = profile['user_id']
                pr  = admin.table('profiles').select('email,name').eq('id', uid).execute()
                if not pr.data:
                    continue
                email = pr.data[0].get('email', '')
                name  = profile.get('preferred_name') or pr.data[0].get('name', '')
                if not email:
                    continue
                send_student_summary(email, name, profile)
            except Exception as e:
                print(f"[email] Skipping {profile.get('user_id')}: {e}")

    except Exception as e:
        print(f"[email] run_daily_summaries error: {e}")


def start_scheduler():
    """Start background thread that fires run_daily_summaries at 9 PM BDT (15:00 UTC) daily."""
    import threading
    import time

    def _loop():
        time.sleep(3)  # let Flask finish printing its startup banner first
        while True:
            now    = datetime.now(timezone.utc)
            target = now.replace(hour=15, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            secs = (target - now).total_seconds()
            print(f"[email] Next summary in {secs/3600:.1f}h (at {target.strftime('%Y-%m-%d %H:%M')} UTC)")
            time.sleep(secs)
            run_daily_summaries()

    threading.Thread(target=_loop, daemon=True, name="email-scheduler").start()
