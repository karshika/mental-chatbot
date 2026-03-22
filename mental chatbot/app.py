from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
import os, re, random
from datetime import datetime, timezone
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

# ===================== ROUTES =====================

@app.route("/")
def home():
    user = session.get("user")
    return render_template("home.html", user=user)

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
        url = "https://www.psychologytoday.com/us/therapists"
    elif rec_type == "place":
        url = "https://www.google.com/maps/search/peaceful+parks+near+me"
    return jsonify({"url": url})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)