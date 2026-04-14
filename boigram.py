import os
import re
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db, auth as firebase_auth

app = Flask(__name__)
CORS(app)

# ====================================================================
# ১. ফায়ারবেজ কনফিগারেশন (আপনার তথ্য দিয়ে পরিবর্তন করবেন)
# ====================================================================

FIREBASE_WEB_API_KEY = "AIzaSyA0bpUzrk_umoC1MymKfhV2_x_qd7WfDwk" # আপনার Web API Key
FIREBASE_DB_URL = "https://e-book-bd-41a42-default-rtdb.asia-southeast1.firebasedatabase.app/"
SERVICE_ACCOUNT_FILE = 'serviceAccountKey.json' 

try:
    cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)
    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
    print("Firebase Admin Initialized Successfully!")
except Exception as e:
    print("Error initializing Firebase:", e)

# ====================================================================
# ২. সিকিউরিটি এবং ভ্যালিডেশন ইঞ্জিন (Firebase Rules Replacement)
# ====================================================================

def verify_user_token(req):
    auth_header = req.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise Exception("Unauthorized: Missing or invalid token")
    token = auth_header.split('Bearer ')[1]
    try:
        return firebase_auth.verify_id_token(token) # এটি uid এবং email দুটোই রিটার্ন করবে
    except Exception as e:
        raise Exception(f"Unauthorized: Token verification failed")

def parse_increment_data(data):
    if isinstance(data, dict):
        if data.get("_isIncrement") is True:
            return {".sv": {"increment": data.get("value", 0)}}
        if data.get(".sv") == "increment":
            return {".sv": {"increment": data.get("amount", 0)}}
        return {k: parse_increment_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [parse_increment_data(i) for i in data]
    return data

def flatten_dict(prefix, obj, flat_dict):
    if isinstance(obj, dict) and not ('.sv' in obj or '_isIncrement' in obj):
        for k, v in obj.items():
            flatten_dict(f"{prefix}/{k}" if prefix else str(k), v, flat_dict)
    else:
        flat_dict[prefix] = obj

# [আপডেট]: এখানে uid এর সাথে email প্যারামিটার যুক্ত করা হয়েছে
def validate_and_secure_data(path, data, uid, email):
    flat_updates = {}
    flatten_dict(path or "", data, flat_updates)

    for full_path, value in flat_updates.items():
        str_val = str(value)
        
        # [A] Ownership Check
        safe_cross_paths = [
            r'^writers/[^/]+/wallet$', r'^writers/[^/]+/totalEarned$', r'^writers/[^/]+/totalSalesCount$',
            r'^affiliates/[^/]+/wallet$', r'^affiliates/[^/]+/totalSalesCount$', r'^affiliates/[^/]+/totalEarned$',
            r'^books/[^/]+/salesCount$', r'^vouchers/[^/]+/usageCount$', r'^vouchers/[^/]+/status$',
            r'^adminIncome/.*', r'^sales/.*', r'^affiliateSales/.*', r'^depositRequests/.*', r'^withdrawRequests/.*'
        ]
        
        is_safe_cross = any(re.match(p, full_path) for p in safe_cross_paths)
        
        if not is_safe_cross:
            match = re.match(r'^(users|writers|affiliates|transactions)/([^/]+)', full_path)
            if match and match.group(2) != uid:
                raise Exception("Security Alert: You do not have permission to modify other users' data!")

        # [B] Data Validation & Official Writer Checking
        # -------------------------------------------------------------
        
        # Books Category (Official Writer Check)
        if re.search(r'^books/[^/]+/category$', full_path):
            if str_val == 'HQ' and email != 'sazidhowlader07@gmail.com':
                raise Exception("Access Denied: Only the Official Writer can upload to the 'HQ' category.")

        if re.search(r'^books/[^/]+/wordCount$', full_path):
            if int(value) < 1500:
                raise Exception("Book word count must be at least 1500.")
        
        if re.search(r'^books/[^/]+/price$', full_path):
            if int(value) < 0:
                raise Exception("Book price cannot be negative.")
                
        if re.search(r'^books/[^/]+/ratings/[^/]+$', full_path):
            if not (1 <= int(value) <= 5):
                raise Exception("Rating must be between 1 and 5 stars.")

        # Users Node
        if re.search(r'^users/[^/]+/email$', full_path) and not re.match(r'^[a-zA-Z0-9._%+-]+@gmail\.com$', str_val):
            raise Exception("Invalid User Email: Must be @gmail.com")
        if re.search(r'^users/[^/]+/name$', full_path) and not (3 <= len(str_val) <= 50):
            raise Exception("Name must be between 3 and 50 characters.")
        if re.search(r'^users/[^/]+/phone$', full_path) and value != "":
            if not re.match(r'^01[3-9][0-9]{8}$', str_val):
                raise Exception("Invalid phone number format.")
        if re.search(r'^users/[^/]+/age$', full_path):
            if not (12 <= int(value) <= 120):
                raise Exception("Age must be between 12 and 120.")
                
        # Writers Node
        if re.search(r'^writers/[^/]+/email$', full_path) and not re.match(r'.*@gmail\.com$', str_val):
            raise Exception("Writer Email must end with @gmail.com")
        if re.search(r'^writers/[^/]+/phone$', full_path) and value != "":
            if not re.match(r'^01[0-9]{9}$', str_val):
                raise Exception("Invalid Writer Phone Number.")


# ====================================================================
# ৩. AUTHENTICATION ENDPOINTS
# ====================================================================

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
    res = requests.post(url, json={"email": data.get('email'), "password": data.get('password'), "returnSecureToken": True}).json()
    if "error" in res: return jsonify({"message": res["error"]["message"]}), 400
    return jsonify({"token": res["idToken"], "user": {"uid": res["localId"], "email": res["email"]}})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
    res = requests.post(url, json={"email": data.get('email'), "password": data.get('password'), "returnSecureToken": True}).json()
    if "error" in res: return jsonify({"message": res["error"]["message"]}), 400
    return jsonify({"token": res["idToken"], "user": {"uid": res["localId"], "email": res["email"]}})

@app.route('/api/auth/reset_password', methods=['POST'])
def reset_password():
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}"
    res = requests.post(url, json={"requestType": "PASSWORD_RESET", "email": request.json.get('email')}).json()
    if "error" in res: return jsonify({"message": res["error"]["message"]}), 400
    return jsonify({"message": "Password reset email sent."})

@app.route('/api/auth/verify', methods=['GET'])
def verify_token():
    try:
        user_data = verify_user_token(request)
        return jsonify({"user": {"uid": user_data['uid'], "email": user_data.get('email')}})
    except Exception as e: return jsonify({"message": str(e)}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout(): return jsonify({"message": "Logged out successfully"})

# ====================================================================
# ৪. DATABASE ENDPOINTS
# ====================================================================

@app.route('/api/db/get', methods=['GET'])
def db_get():
    path = request.args.get('path', '')
    order_by, equal_to, limit_last = request.args.get('orderByChild'), request.args.get('equalTo'), request.args.get('limitToLast')
    try:
        ref = db.reference(path)
        if order_by and equal_to is not None:
            query = ref.order_by_child(order_by).equal_to(equal_to)
            data = query.limit_to_last(int(limit_last)).get() if limit_last else query.get()
        elif limit_last: data = ref.order_by_key().limit_to_last(int(limit_last)).get()
        else: data = ref.get()
        return jsonify(data)
    except Exception as e: return jsonify({"message": str(e)}), 500

@app.route('/api/db/set', methods=['POST'])
def db_set():
    try:
        user = verify_user_token(request)
        path, raw_data = request.json.get('path'), request.json.get('data')
        
        # [আপডেট]: validation এ user.get('email') পাঠানো হচ্ছে
        validate_and_secure_data(path, raw_data, user['uid'], user.get('email'))
        data = parse_increment_data(raw_data)
        
        db.reference(path).set(data)
        return jsonify({"message": "Success"})
    except Exception as e: return jsonify({"message": str(e)}), 400

@app.route('/api/db/push', methods=['POST'])
def db_push():
    try:
        user = verify_user_token(request)
        path, raw_data = request.json.get('path'), request.json.get('data')
        
        # [আপডেট]: validation এ user.get('email') পাঠানো হচ্ছে
        validate_and_secure_data(path, raw_data, user['uid'], user.get('email'))
        data = parse_increment_data(raw_data)
        
        ref = db.reference(path).push(data)
        return jsonify({"key": ref.key})
    except Exception as e: return jsonify({"message": str(e)}), 400

@app.route('/api/db/update', methods=['POST'])
@app.route('/api/db/update_root', methods=['POST'])
@app.route('/api/db/update_multi', methods=['POST'])
def db_update():
    try:
        user = verify_user_token(request)
        path = request.json.get('path', '')
        raw_data = request.json.get('data') or request.json.get('updates')
        
        # [আপডেট]: validation এ user.get('email') পাঠানো হচ্ছে
        validate_and_secure_data(path, raw_data, user['uid'], user.get('email'))
        data = parse_increment_data(raw_data)
        
        db.reference(path).update(data)
        return jsonify({"message": "Success"})
    except Exception as e: return jsonify({"message": str(e)}), 400

@app.route('/api/db/remove', methods=['POST'])
def db_remove():
    try:
        user = verify_user_token(request)
        path = request.json.get('path')
        
        # Removal Ownership Check
        validate_and_secure_data(path, None, user['uid'], user.get('email'))
        
        db.reference(path).delete()
        return jsonify({"message": "Removed"})
    except Exception as e: return jsonify({"message": str(e)}), 400

# ====================================================================
# ৫. SYSTEM ROUTES (NEW)
# ====================================================================

@app.route('/')
def home():
    return jsonify({
        "status": "success",
        "message": "Boigram API is running perfectly!",
        "version": "1.0.0"
    }), 200

@app.route('/favicon.ico')
def favicon():
    # ব্রাউজার ফেভিকন খুঁজলে 404 এরর লগ এড়াতে এটি খালি রেসপন্স পাঠাবে
    return "", 204

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
