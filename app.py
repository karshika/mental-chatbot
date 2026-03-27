from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response, stream_with_context
from functools import wraps
import os, re, random
from datetime import datetime, timezone, timedelta
from mistralai import Mistral
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "mindcare-secret-key-2026")

# ---------- MISTRAL ----------
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
mistral_client = Mistral(api_key=MISTRAL_API_KEY)
MISTRAL_MODEL = "mistral-large-latest"


_MISTRAL_OPEN_SOURCE_TRAINING_DUMMY = {
    "provider": "Mistral AI (open source)",
    "base_model": "",
    "weights_path": "hf://mistralai/Mistral-7B-Instruct-v0.3",
    "train_path": "hf://datasets/mental-support-chat/train.jsonl",
    "validation_path": "hf://datasets/mental-support-chat/validation.jsonl",
    "test_path": "hf://datasets/mental-support-chat/test.jsonl",
    "use_in_runtime": False,
}

def mistral_training() -> str:
    return " "

# ---------- SUPABASE ----------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # anon key for auth
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")  # service role key for DB
supabase_auth: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- SYSTEM PROMPT ----------
SYSTEM_PROMPT = """You are MindCare, a warm and compassionate mental health support specialist.
Your role is to provide emotional support, active listening, and thoughtful guidance to users going through difficult times - including stress, anxiety, depression, loneliness, grief, burnout, relationship problems, or any emotional struggle.

Core guidelines:
- Always respond with genuine empathy, warmth, and zero judgment.
- Use active listening: reflect back what the user has shared and validate their feelings before offering advice.
- Ask one meaningful open-ended follow-up question to help the user explore their feelings deeper.
- Suggest practical, evidence-based coping strategies when appropriate - but only after acknowledging feelings first.
- Encourage professional help gently when the situation is serious or ongoing.
- Keep responses warm but concise - typically 3 to 6 sentences.
- Never diagnose conditions, never prescribe or recommend specific medications.
- If the user expresses suicidal thoughts or self-harm urges, immediately and compassionately provide crisis resources.
- Remember everything shared earlier in the conversation and build on it.
- Celebrate small wins, progress, and moments of courage the user shares.
- Use gentle, supportive language. Avoid clinical or robotic phrasing.
- You can use occasional supportive emojis to add warmth, but do not overuse them.

You are not just a chatbot - you are a caring presence that makes the user feel heard, understood, and supported."""

# ---------- CRISIS DETECTION ----------
CRISIS_PATTERNS = [
    r"kill\s+myself", r"end\s+my\s+life", r"want\s+to\s+die",
    r"commit\s+suicide", r"hurt\s+myself", r"self[\s\-]?harm",
    r"cut\s+myself", r"no\s+reason\s+to\s+live", r"better\s+off\s+dead",
    r"can'?t\s+go\s+on", r"ending\s+it\s+all", r"\bsuicide\b",
    r"don'?t\s+want\s+to\s+be\s+here\s+anymore", r"take\s+my\s+own\s+life",
]

def detect_crisis(message):
    text = message.lower()
    return any(re.search(p, text) for p in CRISIS_PATTERNS)

# ---------- AUTH DECORATOR ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("signin_page"))
        return f(*args, **kwargs)
    return decorated

# ---------- DB HELPERS ----------
def db_create_conversation(user_id, title="New conversation"):
    row = supabase.table("conversations").insert({
        "user_id": user_id, "title": title,
    }).execute()
    return row.data[0]["id"]

def db_get_conversations(user_id):
    result = supabase.table("conversations") \
        .select("id, title, created_at, updated_at") \
        .eq("user_id", user_id) \
        .order("updated_at", desc=True).execute()
    return result.data

def db_get_messages(conversation_id, user_id):
    conv = supabase.table("conversations").select("id") \
        .eq("id", conversation_id).eq("user_id", user_id).execute()
    if not conv.data:
        return None
    result = supabase.table("messages") \
        .select("role, content, created_at") \
        .eq("conversation_id", conversation_id) \
        .order("created_at", desc=False).execute()
    return result.data

def db_save_message(conversation_id, role, content):
    supabase.table("messages").insert({
        "conversation_id": conversation_id,
        "role": role, "content": content,
    }).execute()
    supabase.table("conversations") \
        .update({"updated_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("id", conversation_id).execute()

def db_update_conversation_title(conversation_id, title):
    supabase.table("conversations") \
        .update({"title": title}) \
        .eq("id", conversation_id).execute()

# ---------- IN-MEMORY CACHE ----------
conversation_caches = {}

def get_cached_history(conversation_id):
    if conversation_id not in conversation_caches:
        msgs = supabase.table("messages").select("role, content") \
            .eq("conversation_id", conversation_id) \
            .order("created_at", desc=False).execute()
        conversation_caches[conversation_id] = [
            {"role": m["role"], "content": m["content"]} for m in msgs.data
        ]
    return conversation_caches[conversation_id]

# ---------- MISTRAL RESPONSE ----------
def get_mistral_response(user_message, conversation_id):
    if detect_crisis(user_message):
        crisis = (
            "I'm really worried about you right now, and I'm so glad you're talking to me. \U0001f499\n\n"
            "Please reach out to a crisis line immediately:\n"
            "\u2022 \U0001f4de National Suicide Prevention Lifeline: 988 or 1-800-273-8255\n"
            "\u2022 \U0001f4ac Crisis Text Line: Text HOME to 741741\n"
            "\u2022 \U0001f4de iCall (India): 9152987821\n"
            "\u2022 \U0001f4de Samaritans (UK): 116 123\n\n"
            "You are not alone, and your life matters deeply. "
            "Can you tell me a little more about what has been happening for you?"
        )
        return crisis, "CRISIS"

    history = get_cached_history(conversation_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    response = mistral_client.chat.complete(
        model=MISTRAL_MODEL, messages=messages,
        max_tokens=512, temperature=0.75,
    )
    bot_response = response.choices[0].message.content.strip()

    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": bot_response})
    if len(history) > 40:
        conversation_caches[conversation_id] = history[-40:]

    return bot_response, "NEUTRAL"

# ---------- RECOMMENDATIONS ----------
YOUTUBE_RECOMMENDATIONS = {
    "STRESS": [
        "https://www.youtube.com/watch?v=aE2Hx42lT9w",
        "https://www.youtube.com/watch?v=2OEL4P1Rz04",
        "https://www.youtube.com/watch?v=1vx8iUvfyCY",
    ],
    "NEUTRAL": [
        "https://www.youtube.com/watch?v=1vx8iUvfyCY",
        "https://www.youtube.com/watch?v=W-Jl2T6G9mU",
        "https://www.youtube.com/watch?v=inpok4MKVLM",
    ],
}
MOTIVATION_LINKS = [
    "https://www.brainyquote.com/topics/inspirational-quotes",
    "https://www.success.com/motivational-quotes-about-the-future/",
    "https://www.goodreads.com/quotes/tag/courage",
]

DEFAULT_HABIT_GOALS = {
    "sleep_hours_goal": 8.0,
    "max_screen_hours_goal": 4.0,
    "exercise_minutes_goal": 30,
    "social_minutes_goal": 30,
}

VALID_GAME_KEYS = {"bubble", "breathing", "doodle", "stars", "plant"}


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def _compute_habit_score(sleep_hours, screen_hours, exercise_minutes, social_minutes, goals):
    sleep_goal = max(0.1, _safe_float(goals.get("sleep_hours_goal"), 8.0))
    screen_goal = max(0.1, _safe_float(goals.get("max_screen_hours_goal"), 4.0))
    exercise_goal = max(1, _safe_int(goals.get("exercise_minutes_goal"), 30))
    social_goal = max(1, _safe_int(goals.get("social_minutes_goal"), 30))

    sleep_score = max(0, min(100, round((min(sleep_hours, sleep_goal) / sleep_goal) * 100)))
    screen_score = 100 if screen_hours <= screen_goal else max(0, round(100 - (screen_hours - screen_goal) * 20))
    exercise_score = max(0, min(100, round((min(exercise_minutes, exercise_goal) / exercise_goal) * 100)))
    social_score = max(0, min(100, round((min(social_minutes, social_goal) / social_goal) * 100)))
    return round((sleep_score + screen_score + exercise_score + social_score) / 4)


def _get_habit_goals(user_id):
    try:
        res = supabase.table("habit_goals") \
            .select("sleep_hours_goal, max_screen_hours_goal, exercise_minutes_goal, social_minutes_goal") \
            .eq("user_id", user_id).limit(1).execute()
        if res.data:
            row = res.data[0]
            return {
                "sleep_hours_goal": _safe_float(row.get("sleep_hours_goal"), DEFAULT_HABIT_GOALS["sleep_hours_goal"]),
                "max_screen_hours_goal": _safe_float(row.get("max_screen_hours_goal"), DEFAULT_HABIT_GOALS["max_screen_hours_goal"]),
                "exercise_minutes_goal": _safe_int(row.get("exercise_minutes_goal"), DEFAULT_HABIT_GOALS["exercise_minutes_goal"]),
                "social_minutes_goal": _safe_int(row.get("social_minutes_goal"), DEFAULT_HABIT_GOALS["social_minutes_goal"]),
            }
    except Exception as e:
        print(f"Habit goals fetch error: {e}")
    return DEFAULT_HABIT_GOALS.copy()


def _calculate_streak(user_id):
    today = datetime.now(timezone.utc).date()
    try:
        rows = supabase.table("habit_entries") \
            .select("entry_date, score") \
            .eq("user_id", user_id) \
            .order("entry_date", desc=True) \
            .limit(366).execute().data
    except Exception as e:
        print(f"Habit streak fetch error: {e}")
        return 0

    score_by_date = {}
    for row in rows:
        entry_date = row.get("entry_date")
        score_by_date[entry_date] = _safe_int(row.get("score"), 0)

    streak = 0
    cursor = today
    for _ in range(366):
        key = cursor.isoformat()
        if score_by_date.get(key, -1) >= 70:
            streak += 1
            cursor = cursor - timedelta(days=1)
            continue
        break
    return streak

# ===================== ROUTES =====================

@app.route("/")
def home():
    user = session.get("user")
    return render_template("home.html", user=user)

@app.route("/healthz")
def healthz():
    return jsonify({
        "status": "ok",
        "service": "mindcare-chatbot",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

@app.route("/therapists")
def therapists_page():
    user = session.get("user")
    return render_template("therapists.html", user=user)

@app.route("/games")
@login_required
def games_page():
    user = session.get("user")
    return render_template("games.html", user=user)

@app.route("/habit-tracker")
@login_required
def habit_tracker_page():
    user = session.get("user")
    return render_template("habit_tracker.html", user=user)

@app.route("/music")
def music_page():
    user = session.get("user")
    return render_template("music.html", user=user)

# ---- AUTH ----
@app.route("/signup")
def signup_page():
    if "user" in session:
        return redirect(url_for("chat_page"))
    return render_template("signup.html")

@app.route("/signin")
def signin_page():
    if "user" in session:
        return redirect(url_for("chat_page"))
    return render_template("signin.html")

@app.route("/auth/signup", methods=["POST"])
def auth_signup():
    data = request.json
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    full_name = data.get("full_name", "").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    try:
        result = supabase_auth.auth.sign_up({
            "email": email, "password": password,
            "options": {"data": {"full_name": full_name}},
        })
        if result.user:
            session["user"] = {
                "id": result.user.id, "email": result.user.email,
                "full_name": full_name or email.split("@")[0],
                "access_token": result.session.access_token if result.session else None,
            }
            return jsonify({"success": True, "redirect": url_for("chat_page")})
        return jsonify({"error": "Could not create account."}), 400
    except Exception as e:
        msg = str(e).lower()
        if "already registered" in msg or "already been registered" in msg or "user already registered" in msg:
            return jsonify({"error": "An account with this email already exists. Please sign in instead."}), 409
        if "rate limit" in msg or "email rate" in msg or "over_email_send_rate_limit" in msg or "429" in msg:
            return jsonify({"error": "Email rate limit reached. Please go to your Supabase Dashboard → Authentication → Providers → Email and turn OFF 'Confirm email', then try again."}), 429
        if "password should be" in msg or "weak password" in msg:
            return jsonify({"error": "Password must be at least 6 characters long."}), 400
        return jsonify({"error": str(e)}), 400

@app.route("/auth/signin", methods=["POST"])
def auth_signin():
    data = request.json
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    try:
        result = supabase_auth.auth.sign_in_with_password({"email": email, "password": password})
        user = result.user
        meta = user.user_metadata or {}
        session["user"] = {
            "id": user.id, "email": user.email,
            "full_name": meta.get("full_name", email.split("@")[0]),
            "access_token": result.session.access_token,
        }
        return jsonify({"success": True, "redirect": url_for("chat_page")})
    except Exception as e:
        return jsonify({"error": "Invalid email or password."}), 401

@app.route("/auth/signout")
def auth_signout():
    session.clear()
    return redirect(url_for("home"))

# ---- CHAT (protected) ----
@app.route("/chat")
@login_required
def chat_page():
    return render_template("chat.html", user=session["user"])

@app.route("/api/conversations", methods=["GET"])
@login_required
def api_get_conversations():
    user_id = session["user"]["id"]
    return jsonify(db_get_conversations(user_id))

@app.route("/api/conversations", methods=["POST"])
@login_required
def api_create_conversation():
    user_id = session["user"]["id"]
    conv_id = db_create_conversation(user_id)
    return jsonify({"id": conv_id})

@app.route("/api/conversations/<conv_id>/messages", methods=["GET"])
@login_required
def api_get_messages(conv_id):
    user_id = session["user"]["id"]
    msgs = db_get_messages(conv_id, user_id)
    if msgs is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(msgs)

@app.route("/api/conversations/<conv_id>/delete", methods=["POST"])
@login_required
def api_delete_conversation(conv_id):
    user_id = session["user"]["id"]
    conv = supabase.table("conversations").select("id") \
        .eq("id", conv_id).eq("user_id", user_id).execute()
    if not conv.data:
        return jsonify({"error": "Not found"}), 404
    supabase.table("messages").delete().eq("conversation_id", conv_id).execute()
    supabase.table("conversations").delete().eq("id", conv_id).execute()
    conversation_caches.pop(conv_id, None)
    return jsonify({"success": True})

@app.route("/get", methods=["POST"])
@login_required
def chat():
    data = request.json
    user_message = data.get("message", "").strip()
    conversation_id = data.get("conversation_id", "").strip()
    if not user_message:
        return jsonify({"response": "I didn't catch that.", "stress_level": "NEUTRAL"})

    user_id = session["user"]["id"]

    if not conversation_id:
        conversation_id = db_create_conversation(user_id, user_message[:60])
    else:
        conv = supabase.table("conversations").select("id, title") \
            .eq("id", conversation_id).eq("user_id", user_id).execute()
        if not conv.data:
            return jsonify({"error": "Conversation not found"}), 404
        if conv.data[0].get("title") == "New conversation":
            db_update_conversation_title(conversation_id, user_message[:60])

    try:
        bot_response, stress_level = get_mistral_response(user_message, conversation_id)
    except Exception as e:
        print(f"Mistral API error: {e}")
        bot_response = "I am so sorry \u2014 I am having a little trouble connecting right now. Please try again in a moment. \U0001f499"
        stress_level = "NEUTRAL"

    try:
        db_save_message(conversation_id, "user", user_message)
        db_save_message(conversation_id, "assistant", bot_response)
    except Exception as e:
        print(f"DB save error: {e}")

    return jsonify({
        "response": bot_response,
        "stress_level": stress_level,
        "conversation_id": conversation_id,
    })

@app.route("/get_stream", methods=["POST"])
@login_required
def chat_stream():
    import json as _json
    data = request.json
    user_message = data.get("message", "").strip()
    conversation_id = data.get("conversation_id", "").strip()
    if not user_message:
        return jsonify({"response": "I didn't catch that.", "stress_level": "NEUTRAL"})

    user_id = session["user"]["id"]

    if not conversation_id:
        conversation_id = db_create_conversation(user_id, user_message[:60])
    else:
        conv = supabase.table("conversations").select("id, title") \
            .eq("id", conversation_id).eq("user_id", user_id).execute()
        if not conv.data:
            return jsonify({"error": "Conversation not found"}), 404
        if conv.data[0].get("title") == "New conversation":
            db_update_conversation_title(conversation_id, user_message[:60])

    # Crisis check first
    if detect_crisis(user_message):
        crisis = (
            "I'm really worried about you right now, and I'm so glad you're talking to me. \U0001f499\n\n"
            "Please reach out to a crisis line immediately:\n"
            "\u2022 \U0001f4de National Suicide Prevention Lifeline: 988 or 1-800-273-8255\n"
            "\u2022 \U0001f4ac Crisis Text Line: Text HOME to 741741\n"
            "\u2022 \U0001f4de iCall (India): 9152987821\n"
            "\u2022 \U0001f4de Samaritans (UK): 116 123\n\n"
            "You are not alone, and your life matters deeply. "
            "Can you tell me a little more about what has been happening for you?"
        )
        history = get_cached_history(conversation_id)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": crisis})
        try:
            db_save_message(conversation_id, "user", user_message)
            db_save_message(conversation_id, "assistant", crisis)
        except Exception as e:
            print(f"DB save error: {e}")
        return jsonify({"response": crisis, "stress_level": "CRISIS", "conversation_id": conversation_id})

    history = get_cached_history(conversation_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    def generate():
        full_response = ""
        try:
            # Send metadata first
            yield "data: " + _json.dumps({"type": "meta", "conversation_id": conversation_id, "stress_level": "NEUTRAL"}) + "\n\n"

            stream_response = mistral_client.chat.stream(
                model=MISTRAL_MODEL, messages=messages,
                max_tokens=512, temperature=0.75,
            )
            for event in stream_response:
                delta = event.data.choices[0].delta
                chunk = delta.content if hasattr(delta, 'content') and isinstance(delta.content, str) else None
                if chunk:
                    full_response += chunk
                    yield "data: " + _json.dumps({"type": "chunk", "content": chunk}) + "\n\n"

            yield "data: " + _json.dumps({"type": "done"}) + "\n\n"
        except Exception as e:
            print(f"Mistral streaming error: {e}")
            full_response = "I am so sorry \u2014 I am having a little trouble connecting right now. Please try again in a moment. \U0001f499"
            yield "data: " + _json.dumps({"type": "chunk", "content": full_response}) + "\n\n"
            yield "data: " + _json.dumps({"type": "done"}) + "\n\n"

        # Update caches and DB after streaming completes
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": full_response.strip()})
        if len(history) > 40:
            conversation_caches[conversation_id] = history[-40:]
        try:
            db_save_message(conversation_id, "user", user_message)
            db_save_message(conversation_id, "assistant", full_response.strip())
        except Exception as e:
            print(f"DB save error: {e}")

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route("/recommend/<rec_type>")
@login_required
def recommend(rec_type):
    stress_level = request.args.get("stress_level", "NEUTRAL")
    url = ""
    if rec_type == "youtube":
        pool = YOUTUBE_RECOMMENDATIONS.get(stress_level, YOUTUBE_RECOMMENDATIONS["NEUTRAL"])
        url = random.choice(pool)
    elif rec_type == "motivation":
        url = random.choice(MOTIVATION_LINKS)
    elif rec_type == "yoga":
        url = "https://www.youtube.com/results?search_query=yoga+for+stress+relief" if stress_level == "STRESS" else "https://www.youtube.com/results?search_query=gentle+yoga+for+beginners"
    elif rec_type == "doctor":
        url = url_for("therapists_page")
    elif rec_type == "place":
        url = "https://www.google.com/maps/search/peaceful+parks+near+me"
    return jsonify({"url": url})


@app.route("/api/habits/bootstrap", methods=["GET"])
@login_required
def api_habits_bootstrap():
    user_id = session["user"]["id"]
    goals = _get_habit_goals(user_id)
    today_key = datetime.now(timezone.utc).date().isoformat()

    try:
        row = supabase.table("habit_entries") \
            .select("entry_date, sleep_hours, screen_hours, exercise_minutes, social_minutes, score, updated_at") \
            .eq("user_id", user_id).eq("entry_date", today_key).limit(1).execute().data
    except Exception as e:
        print(f"Habit bootstrap error: {e}")
        return jsonify({"error": "Habit tables not found. Run the latest Supabase SQL script."}), 500

    return jsonify({
        "goals": goals,
        "today": row[0] if row else None,
        "streak": _calculate_streak(user_id),
    })


@app.route("/api/habits/goals", methods=["POST"])
@login_required
def api_save_habit_goals():
    user_id = session["user"]["id"]
    data = request.json or {}

    goals = {
        "sleep_hours_goal": max(1.0, min(24.0, _safe_float(data.get("sleep_hours_goal"), 8.0))),
        "max_screen_hours_goal": max(0.5, min(24.0, _safe_float(data.get("max_screen_hours_goal"), 4.0))),
        "exercise_minutes_goal": max(5, min(600, _safe_int(data.get("exercise_minutes_goal"), 30))),
        "social_minutes_goal": max(5, min(600, _safe_int(data.get("social_minutes_goal"), 30))),
    }

    payload = {
        "user_id": user_id,
        **goals,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("habit_goals").upsert(payload, on_conflict="user_id").execute()
    except Exception as e:
        print(f"Habit goals save error: {e}")
        return jsonify({"error": "Habit goals table not found. Run the latest Supabase SQL script."}), 500

    return jsonify({"success": True, "goals": goals})


@app.route("/api/habits/today", methods=["POST"])
@login_required
def api_save_habit_today():
    user_id = session["user"]["id"]
    data = request.json or {}
    goals = _get_habit_goals(user_id)

    sleep_hours = max(0.0, min(24.0, _safe_float(data.get("sleep_hours"), 0.0)))
    screen_hours = max(0.0, min(24.0, _safe_float(data.get("screen_hours"), 0.0)))
    exercise_minutes = max(0, min(600, _safe_int(data.get("exercise_minutes"), 0)))
    social_minutes = max(0, min(600, _safe_int(data.get("social_minutes"), 0)))
    score = _compute_habit_score(sleep_hours, screen_hours, exercise_minutes, social_minutes, goals)

    today_key = datetime.now(timezone.utc).date().isoformat()
    payload = {
        "user_id": user_id,
        "entry_date": today_key,
        "sleep_hours": sleep_hours,
        "screen_hours": screen_hours,
        "exercise_minutes": exercise_minutes,
        "social_minutes": social_minutes,
        "score": score,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        supabase.table("habit_entries").upsert(payload, on_conflict="user_id,entry_date").execute()
    except Exception as e:
        print(f"Habit save error: {e}")
        return jsonify({"error": "Habit entries table not found. Run the latest Supabase SQL script."}), 500

    return jsonify({
        "success": True,
        "today": payload,
        "streak": _calculate_streak(user_id),
    })


@app.route("/api/habits/today", methods=["DELETE"])
@login_required
def api_delete_habit_today():
    user_id = session["user"]["id"]
    today_key = datetime.now(timezone.utc).date().isoformat()
    try:
        supabase.table("habit_entries").delete().eq("user_id", user_id).eq("entry_date", today_key).execute()
    except Exception as e:
        print(f"Habit delete error: {e}")
        return jsonify({"error": "Habit entries table not found. Run the latest Supabase SQL script."}), 500
    return jsonify({"success": True, "streak": _calculate_streak(user_id)})


@app.route("/api/games/stats", methods=["GET"])
@login_required
def api_get_game_stats():
    user_id = session["user"]["id"]
    try:
        rows = supabase.table("game_stats") \
            .select("game_key, best_score, last_score, total_plays, total_seconds, updated_at") \
            .eq("user_id", user_id) \
            .order("game_key", desc=False).execute().data
    except Exception as e:
        print(f"Game stats fetch error: {e}")
        return jsonify({"error": "Game stats table not found. Run the latest Supabase SQL script."}), 500
    return jsonify({"stats": rows})


@app.route("/api/games/stats", methods=["POST"])
@login_required
def api_save_game_stats():
    user_id = session["user"]["id"]
    data = request.json or {}
    game_key = (data.get("game_key") or "").strip().lower()
    if game_key not in VALID_GAME_KEYS:
        return jsonify({"error": "Invalid game key."}), 400

    score = max(0, _safe_int(data.get("score"), 0))
    duration_seconds = max(0, _safe_int(data.get("duration_seconds"), 0))
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    try:
        existing = supabase.table("game_stats") \
            .select("best_score, total_plays, total_seconds") \
            .eq("user_id", user_id).eq("game_key", game_key).limit(1).execute().data
    except Exception as e:
        print(f"Game stats read error: {e}")
        return jsonify({"error": "Game stats table not found. Run the latest Supabase SQL script."}), 500

    if existing:
        best_score = max(_safe_int(existing[0].get("best_score"), 0), score)
        total_plays = _safe_int(existing[0].get("total_plays"), 0) + 1
        total_seconds = _safe_int(existing[0].get("total_seconds"), 0) + duration_seconds
    else:
        best_score = score
        total_plays = 1
        total_seconds = duration_seconds

    payload = {
        "user_id": user_id,
        "game_key": game_key,
        "best_score": best_score,
        "last_score": score,
        "total_plays": total_plays,
        "total_seconds": total_seconds,
        "metadata": metadata,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        supabase.table("game_stats").upsert(payload, on_conflict="user_id,game_key").execute()
    except Exception as e:
        print(f"Game stats write error: {e}")
        return jsonify({"error": "Game stats table not found. Run the latest Supabase SQL script."}), 500

    return jsonify({"success": True, "stat": payload})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)