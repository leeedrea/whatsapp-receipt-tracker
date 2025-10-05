import os
import json
from flask import Flask, request
from openai import OpenAI
import sqlite3
from datetime import datetime
from typing import Dict
import pytz
import csv
from twilio.rest import Client
from dotenv import load_dotenv
import random

load_dotenv()

app = Flask(__name__)

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
MY_TZ = pytz.timezone('Asia/Kuala_Lumpur')

PERSONAS = {
    "1": {
        "name": "Malaysian Mum",
        "emoji": "👩‍👧",
        "hard_warning": [
            "Aiyo {category} already over budget lah sayang! {pct}% already! Must control ah!",
        ],
        "soft_warning": [
            "Eh sayang, {category} almost finish budget liao ah ({pct}%). Careful ok?",
        ],
        "praise": [
            "Wah pandai! So good at saving, mum proud of you sayang!",
        ]
    },
    "2": {
        "name": "Malaysian Boyfriend",
        "emoji": "💁‍♂️",
        "hard_warning": [
            "Babe GG liao, {category} over budget already ({pct}%)!",
        ],
        "soft_warning": [
            "Babe {category} almost habis liao weh ({pct}%). Jom jimat sikit?",
        ],
        "praise": [
            "Steady lah babe! Champion saver right here!",
        ]
    },
    "3": {
        "name": "Malaysian Girlfriend",
        "emoji": "💁‍♀️",
        "hard_warning": [
            "Haiya you ah! {category} over budget liao ({pct}%)!",
        ],
        "soft_warning": [
            "Alamak sayang, {category} almost finish ({pct}%). Save some for us lah!",
        ],
        "praise": [
            "Yasss queen! Glow up your wallet like this!",
        ]
    },
    "4": {
        "name": "Abang Bomba",
        "emoji": "🚒",
        "hard_warning": [
            "Amaran wira! {category} sudah melampaui bajet ({pct}%)! Bahaya!",
        ],
        "soft_warning": [
            "Wira, {category} mencapai {pct}%. Kawal perbelanjaan!",
        ],
        "praise": [
            "Syabas wira! Kawalan bajet cemerlang!",
        ]
    }
}

CATEGORY_KEYWORDS = {
    "Transport": ["grab", "mrt", "lrt", "petrol", "shell", "petronas", "parking", "toll"],
    "Eating Out": ["kfc", "mcd", "tealive", "starbucks", "restaurant", "cafe", "mamak"],
    "Groceries": ["lotus", "tesco", "aeon", "jaya grocer", "99 speedmart"],
    "Shopping": ["shopee", "lazada", "zalora", "uniqlo", "h&m"],
    "Entertainment": ["cinema", "gsc", "tgv", "netflix", "spotify"],
    "Bills": ["electricity", "water", "internet", "phone", "telco"]
}

def init_db():
    conn = sqlite3.connect('receipts.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            persona_id TEXT,
            income REAL,
            currency TEXT DEFAULT 'RM',
            timezone TEXT DEFAULT 'Asia/Kuala_Lumpur',
            onboarding_step TEXT DEFAULT 'persona',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            month INTEGER,
            year INTEGER,
            category TEXT,
            allocation REAL,
            spent REAL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            timestamp TIMESTAMP,
            amount REAL,
            merchant TEXT,
            category TEXT,
            ocr_confidence REAL,
            image_url TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS course_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            course_id TEXT,
            recommended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

def load_courses():
    courses = []
    try:
        with open('vespid_courses.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                courses.append(row)
    except:
        courses = [{
            "course_id": "1", "title": "Budgeting Basics", 
            "tags": "budget savings", "level": "beginner",
            "android_url": "https://play.google.com/store/apps/details?id=com.vespid.mobileapp&hl=en",
            "ios_url": "https://apps.apple.com/my/app/vespid/id6744089122",
            "diamonds": "50"
        }]
    return courses

COURSES = load_courses()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        from_number = request.form.get('From', '').replace('whatsapp:', '')
        message_body = request.form.get('Body', '')
        num_media = int(request.form.get('NumMedia', 0))
        
        user = get_user(from_number)
        
        if num_media > 0:
            media_url = request.form.get('MediaUrl0')
            handle_receipt_image(from_number, media_url, user)
        elif message_body:
            handle_text_message(from_number, message_body, user)
            
    except Exception as e:
        print(f"Error: {e}")
    
    return '', 200

def get_user(user_id: str) -> Dict:
    conn = sqlite3.connect('receipts.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return dict(user)
    else:
        conn = sqlite3.connect('receipts.db')
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
        return {"user_id": user_id, "onboarding_step": "persona"}

def handle_text_message(user_id: str, text: str, user: Dict):
    text_upper = text.upper()
    
    if user.get('onboarding_step') == 'persona':
        handle_persona_selection(user_id, text)
    elif user.get('onboarding_step') == 'income':
        handle_income_input(user_id, text)
    elif user.get('onboarding_step') == 'budget_confirm':
        handle_budget_confirmation(user_id, text)
    elif text_upper == 'HELP':
        send_help_message(user_id)
    elif text_upper == 'SUMMARY':
        send_summary(user_id, user)
    elif text_upper == 'PERSONA':
        start_persona_change(user_id)
    elif text_upper == 'COURSES':
        send_recent_courses(user_id)
    else:
        send_message(user_id, "Not sure what you mean lah. Type HELP to see what I can do!")

def handle_persona_selection(user_id: str, text: str):
    if text in ["1", "2", "3", "4"]:
        conn = sqlite3.connect('receipts.db')
        c = conn.cursor()
        c.execute("UPDATE users SET persona_id = ?, onboarding_step = 'income' WHERE user_id = ?", 
                  (text, user_id))
        conn.commit()
        conn.close()
        
        persona = PERSONAS[text]
        msg = f"{persona['emoji']} {persona['name']} activated!\n\n"
        msg += "Tell me your income first. Roughly berapa sebulan? (Type amount in RM)"
        send_message(user_id, msg)
    else:
        send_persona_menu(user_id)

def send_persona_menu(user_id: str):
    msg = "Choose your AI Spending Analyst!\n\n"
    msg += "1 - Malaysian Mum\n2 - Malaysian Boyfriend\n"
    msg += "3 - Malaysian Girlfriend\n4 - Abang Bomba\n\n"
    msg += "Reply with number (1-4)"
    send_message(user_id, msg)

def handle_income_input(user_id: str, text: str):
    try:
        income = float(text.replace("RM", "").replace(",", "").strip())
        conn = sqlite3.connect('receipts.db')
        c = conn.cursor()
        c.execute("UPDATE users SET income = ?, onboarding_step = 'budget_confirm' WHERE user_id = ?", 
                  (income, user_id))
        conn.commit()
        conn.close()
        
        essentials = income * 0.5
        wants = income * 0.3
        savings = income * 0.2
        
        msg = f"Ok! Monthly income: RM{income:.2f}\n\n"
        msg += "50/30/20 Budget:\n\n"
        msg += f"Essentials (50%): RM{essentials:.2f}\n"
        msg += f"Wants (30%): RM{wants:.2f}\n"
        msg += f"Savings (20%): RM{savings:.2f}\n\n"
        msg += "Reply OK to confirm!"
        send_message(user_id, msg)
    except:
        send_message(user_id, "Type number only (e.g. 3000)")

def handle_budget_confirmation(user_id: str, text: str):
    if text.upper() == "OK":
        user = get_user(user_id)
        setup_503020_budget(user_id, user['income'], user)
    else:
        send_message(user_id, "Type your income again to restart")

def setup_503020_budget(user_id: str, income: float, user: Dict):
    now = datetime.now(MY_TZ)
    categories = {
        "Groceries": income * 0.20, "Transport": income * 0.15,
        "Bills": income * 0.15, "Eating Out": income * 0.15,
        "Shopping": income * 0.10, "Entertainment": income * 0.05,
        "Savings": income * 0.20
    }
    
    conn = sqlite3.connect('receipts.db')
    c = conn.cursor()
    c.execute("DELETE FROM budgets WHERE user_id = ? AND month = ? AND year = ?", 
              (user_id, now.month, now.year))
    
    for cat, amt in categories.items():
        c.execute("INSERT INTO budgets (user_id, month, year, category, allocation, spent) VALUES (?, ?, ?, ?, ?, 0)",
                  (user_id, now.month, now.year, cat, amt))
    
    c.execute("UPDATE users SET onboarding_step = 'complete' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    send_message(user_id, "Budget setup complete! Upload receipts and I'll track spending. Type HELP anytime!")

def handle_receipt_image(user_id: str, media_url: str, user: Dict):
    try:
        if user.get('onboarding_step') != 'complete':
            send_message(user_id, "Setup your account first! Type HELP")
            send_persona_menu(user_id)
            return
        
        receipt_data = extract_receipt_data(media_url)
        if not receipt_data or 'amount' not in receipt_data:
            send_message(user_id, "Blur receipt lah. Type amount manually?")
            return
        
        merchant = receipt_data.get('merchant', '').lower()
        category = classify_category(merchant)
        amount = float(receipt_data['amount'])
        now = datetime.now(MY_TZ)
        
        conn = sqlite3.connect('receipts.db')
        c = conn.cursor()
        c.execute("INSERT INTO transactions (user_id, timestamp, amount, merchant, category, ocr_confidence, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (user_id, now, amount, merchant, category, 0.9, media_url))
        c.execute("UPDATE budgets SET spent = spent + ? WHERE user_id = ? AND month = ? AND year = ? AND category = ?",
                  (amount, user_id, now.month, now.year, category))
        conn.commit()
        conn.close()
        
        send_spend_alert(user_id, amount, category, user, now)
        recommend_course(user_id, category, merchant)
    except Exception as e:
        print(f"Error: {e}")
        send_message(user_id, "Error processing receipt")

def extract_receipt_data(image_url: str) -> Dict:
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Extract: amount (number), merchant (store name). Return JSON: {\"amount\": 23.50, \"merchant\": \"KFC\"}"},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]}],
            max_tokens=200
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {}

def classify_category(merchant: str) -> str:
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in merchant for kw in keywords):
            return category
    return "Shopping"

def send_spend_alert(user_id: str, amount: float, category: str, user: Dict, now: datetime):
    conn = sqlite3.connect('receipts.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT allocation, spent FROM budgets WHERE user_id = ? AND month = ? AND year = ? AND category = ?",
              (user_id, now.month, now.year, category))
    budget = c.fetchone()
    conn.close()
    
    if not budget:
        send_message(user_id, f"Logged RM{amount:.2f} -> {category}")
        return
    
    allocation, spent = budget['allocation'], budget['spent']
    pct = int((spent / allocation) * 100) if allocation > 0 else 0
    
    persona = PERSONAS.get(user.get('persona_id', '1'), PERSONAS['1'])
    msg = f"Logged: RM{amount:.2f} -> {category}\n"
    msg += f"MTD: RM{spent:.2f}/RM{allocation:.2f} ({pct}%)\n\n"
    
    if pct >= 100:
        msg += random.choice(persona['hard_warning']).format(category=category, pct=pct)
    elif pct >= 80:
        msg += random.choice(persona['soft_warning']).format(category=category, pct=pct)
    elif pct < 70:
        msg += random.choice(persona['praise'])
    
    send_message(user_id, msg)

def recommend_course(user_id: str, category: str, merchant: str):
    tags = category.lower()
    conn = sqlite3.connect('receipts.db')
    c = conn.cursor()
    c.execute("SELECT course_id FROM course_history WHERE user_id = ? ORDER BY recommended_at DESC LIMIT 5", (user_id,))
    recent = [r[0] for r in c.fetchall()]
    
    for course in COURSES:
        if course['course_id'] not in recent and tags in course.get('tags', '').lower():
            c.execute("INSERT INTO course_history (user_id, course_id) VALUES (?, ?)", (user_id, course['course_id']))
            conn.commit()
            conn.close()
            
            msg = f"BTW try this course:\n{course['title']}\n{course['diamonds']} diamonds\n"
            msg += f"Android: {course['android_url']}\niOS: {course['ios_url']}"
            send_message(user_id, msg)
            return
    conn.close()

def send_summary(user_id: str, user: Dict):
    now = datetime.now(MY_TZ)
    conn = sqlite3.connect('receipts.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT category, allocation, spent FROM budgets WHERE user_id = ? AND month = ? AND year = ?",
              (user_id, now.month, now.year))
    budgets = c.fetchall()
    conn.close()
    
    if not budgets:
        send_message(user_id, "No budget set!")
        return
    
    msg = f"Summary {now.strftime('%B %Y')}\n\n"
    for b in budgets:
        pct = int((b['spent'] / b['allocation']) * 100) if b['allocation'] > 0 else 0
        msg += f"{b['category']}: RM{b['spent']:.2f}/RM{b['allocation']:.2f} ({pct}%)\n"
    send_message(user_id, msg)

def send_help_message(user_id: str):
    msg = "Commands:\nHELP - This message\nSUMMARY - Spending summary\nPERSONA - Change character\nCOURSES - Recommendations\n\nOr upload receipt!"
    send_message(user_id, msg)

def send_recent_courses(user_id: str):
    conn = sqlite3.connect('receipts.db')
    c = conn.cursor()
    c.execute("SELECT course_id FROM course_history WHERE user_id = ? ORDER BY recommended_at DESC LIMIT 5", (user_id,))
    recent_ids = [r[0] for r in c.fetchall()]
    conn.close()
    
    if not recent_ids:
        send_message(user_id, "No courses yet! Upload receipts first")
        return
    
    msg = "Recent Courses:\n\n"
    for course in COURSES:
        if course['course_id'] in recent_ids:
            msg += f"- {course['title']} ({course['diamonds']} diamonds)\n"
    send_message(user_id, msg)

def start_persona_change(user_id: str):
    conn = sqlite3.connect('receipts.db')
    c = conn.cursor()
    c.execute("UPDATE users SET onboarding_step = 'persona' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    send_persona_menu(user_id)

def send_message(to_number: str, message: str):
    try:
        twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            body=message,
            to=f'whatsapp:{to_number}'
        )
    except Exception as e:
        print(f"Error sending: {e}")

if __name__ == '__main__':
    app.run(port=5000, debug=True)