from flask import Flask, request, jsonify, render_template
import sqlite3
from datetime import datetime
import math
from google import genai
import json
import re
import base64

app = Flask(__name__)

# ─────────────────────────────────────────────
# Gemini client
# ─────────────────────────────────────────────
API_KEY = "AIzaSyC1Sbs-40oE9dNOLo1W2Cp1wA1vH2PSH0I"
client = genai.Client(api_key=API_KEY)

# ─────────────────────────────────────────────
# Haversine distance formula
# ─────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ─────────────────────────────────────────────
# Database initialisation (runs once on startup)
# ─────────────────────────────────────────────
def init_db():
    conn   = sqlite3.connect("doctors.db")
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS doctors")
    cursor.execute("""
    CREATE TABLE doctors (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        name           TEXT,
        specialty      TEXT,
        address        TEXT,
        phone          TEXT,
        rating         REAL,
        available_from TEXT,
        available_to   TEXT,
        available_days TEXT,
        vacation_from  TEXT,
        vacation_to    TEXT,
        latitude       REAL,
        longitude      REAL
    )""")

    doctors_data = [
        ("CRYSTAL DENTAL CLINIC Dr Dridah ep Manaa", "Dentiste",
         "batiment N 05, Cite Cosider, Oued elwahch, Skikda",
         "0556660462", 5.0, "08:30", "16:30", "Mon,Tue,Wed,Thu,Sun",
         "2023-04-10", "2023-04-12", 36.87104754814202, 6.896438109494414),

        ("Cabinet medical Dr S. Hassani ep Bourafa", "General",
         "Cite arc en ciel BT 5 N 2, 21000",
         "0794146371", 5.0, "09:00", "17:00", "Mon,Tue,Thu,Sun",
         "2023-04-10", "2023-04-12", 36.88175748722978, 6.896744485097299),

        ("CRYSTAL DENTAL CLINIC Dr Dridah ep Manaa", "Dentiste",
         "batiment N 05, Cite Cosider, Oued elwahch, Skikda",
         "0556660462", 5.0, "08:30", "16:30", "Mon,Tue,Wed,Thu,Sun",
         "2023-04-10", "2023-04-12", None, None),







        ("CLINIC", "SPECIALTY",
         "Adress",
         "tel", 5.0, "08:30", "16:30", "Mon,Tue,Wed,Thu,Sun",
         "2023-04-10", "2023-04-12", "lat", "lon"),

        ("CLINIC", "SPECIALTY",
         "Adress",
         "tel", 5.0, "08:30", "16:30", "Mon,Tue,Wed,Thu,Sun",
         "2023-04-10", "2023-04-12", "lat", "lon"),

        ("CLINIC", "SPECIALTY",
         "Adress",
         "tel", 5.0, "08:30", "16:30", "Mon,Tue,Wed,Thu,Sun",
         "2023-04-10", "2023-04-12", "lat", "lon"),












    ]

    cursor.executemany("""
    INSERT INTO doctors (name, specialty, address, phone, rating,
        available_from, available_to, available_days,
        vacation_from, vacation_to, latitude, longitude)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, doctors_data)

    conn.commit()
    conn.close()
    print("Database initialised.")

# ─────────────────────────────────────────────
# Check availability for a single doctor tuple
# ─────────────────────────────────────────────
def check_availability(doc):
    now          = datetime.now()
    current_time = now.strftime("%H:%M")
    current_day  = now.strftime("%a")
    current_date = now.strftime("%Y-%m-%d")

    avail_from = doc[6]
    avail_to   = doc[7]
    days_list  = doc[8].split(",")
    vac_from   = doc[9]
    vac_to     = doc[10]

    if vac_from and vac_to and vac_from <= current_date <= vac_to:
        return "On vacation"
    elif current_day in days_list and avail_from <= current_time <= avail_to:
        return "Available now"
    else:
        return "Not available"

# ─────────────────────────────────────────────
# Ask Gemini — text or text+image
# ─────────────────────────────────────────────
def ask_llm(user_message, remembered_specialty=None, image_b64=None, image_mime=None):
    """
    Returns:
      response   - friendly reply
      specialty  - detected specialty
      db_intent  - null | nearest | rating | availability
    """
    context = f"Previously detected specialty: {remembered_specialty}." if remembered_specialty else ""

    # ── FIXED PROMPT ────────────────────────────────────────────────────────
    prompt = f"""You are a friendly medical assistant chatbot. {context}

AVAILABLE SPECIALTIES (use EXACTLY these values):
- General       → for: fever, cold, flu, fatigue, headache, body aches, general checkup, blood pressure, diabetes, infections
- Dentiste      → for: toothache, tooth pain, gum problems, dental cavity, broken tooth, dental cleaning, jaw pain
- Cardiologue   → for: chest pain, heart palpitations, shortness of breath, high blood pressure, irregular heartbeat

SYMPTOM → SPECIALTY MAPPING EXAMPLES:
- "my tooth hurts" → Dentiste
- "I have a toothache" → Dentiste
- "I have chest pain" → Cardiologue  
- "my heart is racing" → Cardiologue
- "I have a fever" → General
- "I feel tired all the time" → General
- "headache for days" → General
- "bleeding gums" → Dentiste

RULES:
1. When user describes symptoms → detect specialty, set db_intent to null, reply warmly and mention the specialist type they need, then suggest they ask for doctors (e.g. "Would you like me to find you the nearest dentist?").
2. When user asks for nearest/closest doctor → db_intent = "nearest"
3. When user asks for best/highest rated → db_intent = "rating"
4. When user asks for available/open now → db_intent = "availability"
5. When user asks for doctors without specifying specialty → reuse the remembered specialty from context.
6. Greetings or unrelated text → specialty = null, db_intent = null
7. If image is provided → analyze it medically, detect specialty from visual symptoms.

Return ONLY valid JSON, no markdown, no extra text:
{{
  "response": "Friendly reply in the same language the user used (1-3 sentences). If symptoms detected, mention the specialist and suggest next step.",
  "specialty": "Exactly one of: General, Dentiste, Cardiologue — or null if not medical",
  "db_intent": "Exactly one of: nearest, rating, availability — or null"
}}

User message: {user_message}
"""

    try:
        # Build content parts
        contents = [prompt]

        # ── FIX #6: Actually send image to Gemini if provided ───────────────
        if image_b64 and image_mime:
            contents = [
                {"role": "user", "parts": [
                    {"inline_data": {"mime_type": image_mime, "data": image_b64}},
                    {"text": prompt}
                ]}
            ]
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config={"response_mime_type": "application/json"}
            )
        else:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )

        raw = response.text.strip()
        print("LLM RAW:", raw)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(match.group()) if match else {}

    except Exception as e:
        print("LLM ERROR:", e)
        return {
            "response": "Sorry, I had trouble understanding that. Please try again.",
            "specialty": None,
            "db_intent": None
        }

# ─────────────────────────────────────────────
# Query and sort doctors from DB
# ─────────────────────────────────────────────
def query_doctors(specialty, db_intent, user_lat=None, user_lon=None):
    if not specialty:
        return []

    conn   = sqlite3.connect("doctors.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM doctors WHERE specialty = ?", (specialty,))
    docs = cursor.fetchall()
    conn.close()

    if not docs:
        return []

    if db_intent == "nearest" and user_lat and user_lon:
        docs = sorted(docs, key=lambda d: (
            haversine(user_lat, user_lon, d[11], d[12]) if (d[11] and d[12]) else float("inf")
        ))
    elif db_intent == "rating":
        docs = sorted(docs, key=lambda d: d[5], reverse=True)
    elif db_intent == "availability":
        priority_map = {"Available now": 0, "Not available": 1, "On vacation": 2}
        docs = sorted(docs, key=lambda d: (
            priority_map.get(check_availability(d), 3), -d[5]
        ))

    result = []
    for d in docs[:5]:
        entry = {
            "name":      d[1],
            "specialty": d[2],
            "address":   d[3],
            "phone":     d[4],
            "rating":    d[5],
            "hours":     f"{d[6]} - {d[7]}",
            "days":      d[8],
            "status":    check_availability(d),
        }
        if db_intent == "nearest" and user_lat and user_lon and d[11] and d[12]:
            entry["distance_km"] = round(haversine(user_lat, user_lon, d[11], d[12]), 2)
        result.append(entry)

    return result

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("chatbot.html")

@app.route("/location")
def location():
    return render_template("location.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}

    user_msg             = data.get("message", "")
    user_lat             = data.get("lat")
    user_lon             = data.get("lon")
    remembered_specialty = data.get("remembered_specialty")
    image_b64            = data.get("image_b64")    # NEW: base64 image from frontend
    image_mime           = data.get("image_mime")   # NEW: e.g. "image/jpeg"

    # ── FIX #2: Validate location BEFORE calling LLM ────────────────────────
    # We'll do a quick pre-check on the message to see if "nearest" is likely
    nearest_keywords = ["nearest", "closest", "near me", "أقرب", "القريب", "proche", "plus proche"]
    likely_nearest = any(kw in user_msg.lower() for kw in nearest_keywords)

    if likely_nearest and (not user_lat or not user_lon):
        return jsonify({
            "response": "📍 To find the nearest doctor, I need your location first. Please tap the location button (📍) below and set your position on the map, then try again.",
            "specialty": remembered_specialty,
            "db_intent": None,
            "doctors": [],
            "needs_location": True   # frontend can use this to highlight the location button
        })

    # 1. Ask LLM
    llm = ask_llm(user_msg, remembered_specialty, image_b64, image_mime)

    bot_response = llm.get("response", "I'm here to help!")

    # ── FIX #3: Better specialty resolution ─────────────────────────────────
    llm_specialty = llm.get("specialty")
    # Only override remembered specialty if LLM returned something valid
    if llm_specialty and llm_specialty in ("General", "Dentiste", "Cardiologue"):
        specialty = llm_specialty
    else:
        specialty = remembered_specialty  # keep previous

    db_intent = llm.get("db_intent")

    # ── FIX #2b: Double-check location when db_intent == nearest ────────────
    if db_intent == "nearest" and (not user_lat or not user_lon):
        return jsonify({
            "response": "📍 To find the nearest doctor, I need your location. Please use the location button (📍) below to set your position, then ask again.",
            "specialty": specialty,
            "db_intent": None,
            "doctors": [],
            "needs_location": True
        })

    # 2. Query DB only when db_intent is set
    doctors = []
    if db_intent and specialty:
        doctors = query_doctors(specialty, db_intent, user_lat, user_lon)
        if not doctors:
            bot_response += f" Unfortunately I couldn't find any {specialty} doctors in the database."

    # 3. Return to frontend
    return jsonify({
        "response":  bot_response,
        "specialty": specialty,
        "db_intent": db_intent,
        "doctors":   doctors,
    })

# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True)