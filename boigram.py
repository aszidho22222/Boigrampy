import os
import json
import uuid
import time
import threading
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import jwt

# ==========================================
# 1. CONFIGURATIONS & SECURITY SECRETS
# ==========================================
app = Flask(__name__)
# ফ্রন্টএন্ড থেকে সব রিকোয়েস্ট অ্যালাও করার জন্য CORS
CORS(app, resources={r"/api/*": {"origins": "*"}})

# সিক্রেট কি (JWT টোকেন তৈরি করার জন্য)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-boigram-key-2024')

# টেলিগ্রাম বট কনফিগারেশন
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8742123220:AAFXBJgG9kQ5he7IHz1P3C7oWQsDFeI0RKQ')
TELEGRAM_ADMIN_CHAT_ID = os.environ.get('TELEGRAM_ADMIN_CHAT_ID', '6334878772')

# ডাটাবেজ ফাইলের নাম
DB_FILE = 'database.json'

# ==========================================
# 2. IN-MEMORY JSON DATABASE ENGINE
# ==========================================
db_lock = threading.Lock()
db_data = {
    "__auth__": {},
    "users": {},
    "writers": {},
    "affiliates": {},
    "books": {},
    "vouchers": {},
    "sales": {},
    "withdrawRequests": {},
    "depositRequests": {},
    "reports": {},
    "settings": {
        "commissions": {"writerPercent": 50, "writerPercentNoVoucher": 70, "affiliatePercent": 20, "userDiscountPercent": 10},
        "writerPercentage": 50,
    }
}

def load_db():
    global db_data
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                db_data = json.load(f)
        except Exception as e:
            print("DB Load Error:", e)

def save_db():
    with db_lock:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(db_data, f, ensure_ascii=False, indent=2)

load_db()

# Firebase Path Resolver Helper
def get_ref(path):
    if not path or path == "/" or path == "": return db_data
    keys = str(path).strip('/').split('/')
    curr = db_data
    for k in keys:
        if k not in curr: return None
        curr = curr[k]
    return curr

def set_ref(path, value):
    if not path or path == "/" or path == "":
        global db_data
        db_data.update(value)
        return
    keys = str(path).strip('/').split('/')
    curr = db_data
    for k in keys[:-1]:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = curr[k]
    curr[keys[-1]] = value
    save_db()

def deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, dict) and k in d and isinstance(d[k], dict):
            if "_isIncrement" in v or ".sv" in v:
                amount = v.get('value', v.get('amount', 0))
                d[k] = d.get(k, 0) + amount
            else:
                deep_update(d[k], v)
        else:
            if v == "{TIMESTAMP}":
                d[k] = int(time.time() * 1000)
            elif isinstance(v, dict) and (".sv" in v or "_isIncrement" in v):
                amount = v.get('value', v.get('amount', 0))
                d[k] = d.get(k, 0) + amount
            else:
                d[k] = v

def update_ref(path, updates):
    if not path or path == "/" or path == "":
        deep_update(db_data, updates)
    else:
        keys = str(path).strip('/').split('/')
        curr = db_data
        for k in keys:
            if k not in curr or not isinstance(curr[k], dict):
                curr[k] = {}
            curr = curr[k]
        deep_update(curr, updates)
    save_db()

# ==========================================
# 3. TELEGRAM BOT ENGINE (Background Task)
# ==========================================
def send_telegram_message(text, chat_id=None):
    # যদি স্পেসিফিক চ্যাট আইডি না থাকে, তবে অ্যাডমিনের কাছে পাঠাবে
    if chat_id is None:
        chat_id = TELEGRAM_ADMIN_CHAT_ID
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

def telegram_polling_loop():
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset: params["offset"] = offset
            res = requests.get(url, params=params, timeout=40).json()
            if res.get("ok"):
                for update in res["result"]:
                    offset = update["update_id"] + 1
                    if "message" in update and "text" in update["message"]:
                        chat_id = str(update["message"]["chat"]["id"])
                        text = update["message"]["text"].strip()
                        lower_text = text.lower()
                        first_name = update["message"]["from"].get("first_name", "User")
                        
                        # ১. সাধারণ রিপ্লাই (বট যে কাজ করছে সেটা বুঝার জন্য)
                        if lower_text in ["/start", "hi", "hello", "হ্যালো", "হাই"]:
                            welcome_msg = f"হ্যালো <b>{first_name}</b>! 👋\nআমি Boigram System Bot. আমি সফলভাবে কাজ করছি এবং অনলাইনে আছি! 🚀"
                            
                            # যদি অ্যাডমিন এই মেসেজ দেয়, তবে সাথে অ্যাডমিন কমান্ডগুলোও দেখিয়ে দিবে
                            if chat_id == str(TELEGRAM_ADMIN_CHAT_ID):
                                welcome_msg += "\n\n<b>অ্যাডমিন কমান্ডস:</b>\n/stats - পরিসংখ্যান\n/pending_books - অপেক্ষমান বই"
                            
                            send_telegram_message(welcome_msg, chat_id=chat_id)
                            
                        # ২. শুধুমাত্র অ্যাডমিন কমান্ডস
                        elif chat_id == str(TELEGRAM_ADMIN_CHAT_ID):
                            if lower_text == "/stats":
                                stats = f"📊 ইউজার: {len(db_data.get('users', {}))}\n" \
                                        f"✍️ রাইটার: {len(db_data.get('writers', {}))}\n" \
                                        f"📢 অ্যাফিলিয়েট: {len(db_data.get('affiliates', {}))}\n" \
                                        f"📚 মোট বই: {len(db_data.get('books', {}))}"
                                send_telegram_message(stats, chat_id=chat_id)
                            elif lower_text == "/pending_books":
                                pending = [b for b in db_data.get('books', {}).values() if b.get('status') == 'pending']
                                send_telegram_message(f"অ্যাপ্রুভালের অপেক্ষায় বই আছে: {len(pending)} টি।", chat_id=chat_id)
        except:
            time.sleep(5)
        time.sleep(2)

# Start Bot in background thread
bot_thread = threading.Thread(target=telegram_polling_loop, daemon=True)
bot_thread.start()

# ==========================================
# 4. AUTHENTICATION ROUTES
# ==========================================
def generate_token(uid, email):
    payload = {
        'uid': uid,
        'email': email,
        'exp': time.time() + (30 * 24 * 60 * 60) # 30 days valid
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({"message": "Email and password required"}), 400
        
    if email in [u['email'] for u in db_data['__auth__'].values()]:
        return jsonify({"message": "Email already registered"}), 400
        
    uid = str(uuid.uuid4())
    db_data['__auth__'][uid] = {
        "email": email,
        "password_hash": generate_password_hash(password)
    }
    save_db()
    
    token = generate_token(uid, email)
    send_telegram_message(f"🔔 <b>নতুন রেজিস্ট্রেশন!</b>\nইমেইল: {email}")
    
    return jsonify({"token": token, "user": {"uid": uid, "email": email}})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    for uid, user_data in db_data['__auth__'].items():
        if user_data['email'] == email:
            if check_password_hash(user_data['password_hash'], password):
                token = generate_token(uid, email)
                return jsonify({"token": token, "user": {"uid": uid, "email": email}})
            else:
                return jsonify({"message": "ভুল পাসওয়ার্ড!"}), 401
                
    return jsonify({"message": "অ্যাকাউন্ট খুঁজে পাওয়া যায়নি!"}), 404

@app.route('/api/auth/verify', methods=['GET'])
def verify_token():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"message": "Token missing"}), 401
    
    token = auth_header.split(" ")[1]
    try:
        decoded = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return jsonify({"user": {"uid": decoded['uid'], "email": decoded['email']}})
    except jwt.ExpiredSignatureError:
        return jsonify({"message": "Token expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"message": "Invalid token"}), 401

@app.route('/api/auth/reset_password', methods=['POST'])
def reset_password():
    email = request.json.get('email')
    return jsonify({"message": f"Password reset link sent to {email}"})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    return jsonify({"message": "Logged out"})

# ==========================================
# 5. DATABASE ROUTES (Firebase Emulator)
# ==========================================
@app.route('/api/db/get', methods=['GET'])
def db_get():
    path = request.args.get('path', '')
    order_by = request.args.get('orderByChild') or request.args.get('orderBy')
    equal_to = request.args.get('equalTo')
    limit_last = request.args.get('limitToLast')
    
    data = get_ref(path)
    
    if data is None:
        return jsonify(None)
        
    if isinstance(data, dict) and order_by:
        filtered_data = {}
        for key, val in data.items():
            if isinstance(val, dict) and str(val.get(order_by)) == str(equal_to):
                filtered_data[key] = val
        data = filtered_data
        
    if isinstance(data, dict) and limit_last:
        try:
            limit = int(limit_last)
            keys = list(data.keys())[-limit:]
            data = {k: data[k] for k in keys}
        except:
            pass

    return jsonify(data)

@app.route('/api/db/set', methods=['POST'])
def db_set():
    payload = request.json
    path = payload.get('path', '')
    data = payload.get('data')
    set_ref(path, data)
    return jsonify({"message": "Success"})

@app.route('/api/db/push', methods=['POST'])
def db_push():
    payload = request.json
    path = payload.get('path', '')
    data = payload.get('data', {})
    
    new_key = "-M" + str(int(time.time() * 1000)) + str(uuid.uuid4())[:8]
    
    if isinstance(data, dict):
        for k, v in data.items():
            if v == "{TIMESTAMP}":
                data[k] = int(time.time() * 1000)
    
    full_path = f"{path}/{new_key}" if path else new_key
    set_ref(full_path, data)
    
    # Notifications for Admin
    if "books" in path and data.get('status') == 'pending':
        send_telegram_message(f"📚 <b>নতুন বই আপলোড!</b>\nবইয়ের নাম: {data.get('title')}\nঅ্যাপ্রুভালের অপেক্ষায়।")
    elif "withdrawRequests" in path:
        send_telegram_message(f"💸 <b>নতুন উইথড্র রিকোয়েস্ট!</b>\nপরিমাণ: {data.get('amount')}৳\nমেথড: {data.get('method')}")
    elif "depositRequests" in path:
        send_telegram_message(f"💰 <b>নতুন ডিপোজিট রিকোয়েস্ট!</b>\nপরিমাণ: {data.get('amount')}৳\nTrxID: {data.get('trxId')}")
    elif "reports" in path:
        send_telegram_message(f"🚩 <b>নতুন রিপোর্ট!</b>\nসমস্যা: {data.get('issue', data.get('message', ''))}")

    return jsonify({"key": new_key, "name": new_key})

@app.route('/api/db/update', methods=['POST'])
def db_update():
    payload = request.json
    path = payload.get('path', '')
    data = payload.get('data', {})
    update_ref(path, data)
    return jsonify({"message": "Update Success"})

@app.route('/api/db/update_root', methods=['POST'])
@app.route('/api/db/update_multi', methods=['POST'])
def db_update_root():
    payload = request.json
    updates = payload.get('updates', {})
    
    with db_lock:
        for path_str, value in updates.items():
            keys = path_str.strip('/').split('/')
            curr = db_data
            for k in keys[:-1]:
                if k not in curr or not isinstance(curr[k], dict):
                    curr[k] = {}
                curr = curr[k]
                
            last_key = keys[-1]
            if isinstance(value, dict) and (".sv" in value or "_isIncrement" in value):
                amount = value.get('value', value.get('amount', 0))
                curr[last_key] = curr.get(last_key, 0) + amount
            elif value == "{TIMESTAMP}":
                curr[last_key] = int(time.time() * 1000)
            else:
                curr[last_key] = value
                
    save_db()
    return jsonify({"message": "Multi-Update Success"})

@app.route('/api/db/remove', methods=['POST'])
def db_remove():
    path = request.json.get('path', '')
    if not path: return jsonify({"message": "Path required"}), 400
    
    keys = path.strip('/').split('/')
    with db_lock:
        curr = db_data
        for k in keys[:-1]:
            if k not in curr: return jsonify({"message": "Path not found"}), 404
            curr = curr[k]
        if keys[-1] in curr:
            del curr[keys[-1]]
    save_db()
    return jsonify({"message": "Deleted"})

# ==========================================
# 6. APP RUNNER
# ==========================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
