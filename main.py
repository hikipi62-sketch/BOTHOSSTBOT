# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests
import random
import hashlib
import secrets
import string
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

# --- Flask Keep Alive ---
from flask import Flask
from threading import Thread
app = Flask('')

@app.route('/')
def home():
    return "🤖 𝐑𝐀𝐉 𝐇𝐎𝐒𝐓 𝐁𝐎𝐓 is Running!"

@app.route('/health')
def health():
    return {"status": "healthy", "uptime": get_uptime()}

def run_flask():
    port = int(os.environ.get("PORT", 9276))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("✅ Flask Keep-Alive server started.")

# --- MongoDB Setup ---
from pymongo import MongoClient
from pymongo import DESCENDING

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/ghostcoder")
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client["ghostcoder"]

col_users        = db["users"]
col_user_files   = db["user_files"]
col_referrals    = db["referrals"]
col_ref_rels     = db["referral_relationships"]
col_redeem_codes = db["redeem_codes"]
col_transactions = db["coin_transactions"]
col_bot_logs     = db["bot_logs"]
col_subscriptions= db["subscriptions"]
col_admins       = db["admins"]

def ensure_indexes():
    col_users.create_index("user_id", unique=True)
    col_user_files.create_index([("user_id", 1), ("file_name", 1)], unique=True)
    col_referrals.create_index("referral_code", unique=True)
    col_ref_rels.create_index("referred_id", unique=True)
    col_redeem_codes.create_index("code", unique=True)
    col_subscriptions.create_index("user_id", unique=True)
    col_admins.create_index("user_id", unique=True)

# --- Configuration (from environment) ---
TOKEN      = os.environ.get("TOKEN", "")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))
ADMIN_ID   = int(os.environ.get("ADMIN_ID",  str(OWNER_ID)))
YOUR_USERNAME  = "@rajfflive"
UPDATE_CHANNEL = "https://t.me/rajfflive"

BASE_DIR        = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
LOGS_DIR        = os.path.join(BASE_DIR, 'logs')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# --- Limits ---
FREE_USER_LIMIT       = 0
SUBSCRIBED_USER_LIMIT = 10
ADMIN_LIMIT           = 50
OWNER_LIMIT           = float('inf')

# --- Coin Settings ---
COIN_PER_FILE_UPLOAD    = 10
COIN_PER_REFERRAL       = 50
COIN_PER_REFERRED_USER  = 30
COIN_PER_DAY_ACTIVE     = 5
COIN_REDEEM_FILE_COST   = 100
COIN_REDEEM_PREMIUM_COST= 500

# --- In-memory state ---
bot_scripts       = {}
user_subscriptions= {}
user_files        = {}
user_coins        = {}
user_referrals    = {}
user_referrers    = {}
referral_stats    = {}
redeem_codes      = {}
active_users      = set()
admin_ids         = {ADMIN_ID, OWNER_ID}
bot_locked        = False
bot_start_time    = datetime.now()

# --- Bot init ---
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, 'bot.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  SYSTEM STATS (psutil optional)
# ─────────────────────────────────────────────
def safe_get_system_stats():
    try:
        import psutil
        try:
            cpu    = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk   = psutil.disk_usage('/')
            return {'cpu': cpu, 'memory_used': memory.percent,
                    'memory_total': format_size(memory.total),
                    'disk_used': disk.percent, 'disk_total': format_size(disk.total),
                    'using_psutil': True}
        except (PermissionError, FileNotFoundError):
            pass
    except ImportError:
        pass
    return {'cpu': 0.0, 'memory_used': 0.0, 'memory_total': "N/A",
            'disk_used': 0.0, 'disk_total': "N/A", 'using_psutil': False}

def safe_is_bot_running_check(script_key):
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            import psutil
            try:
                proc = psutil.Process(script_info['process'].pid)
                return proc.is_running()
            except:
                return script_info['process'].poll() is None
        except ImportError:
            return script_info['process'].poll() is None
    return False

def safe_kill_process_tree(process_info):
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close'):
            try:
                if not process_info['log_file'].closed:
                    process_info['log_file'].close()
            except:
                pass
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            try:
                process.terminate()
                time.sleep(1)
                if process.poll() is None:
                    process.kill()
            except:
                pass
    except Exception as e:
        logger.error(f"Error killing process: {e}")

# ─────────────────────────────────────────────
#  UTILITY
# ─────────────────────────────────────────────
def get_uptime():
    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"

def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"

def get_optimized_response_time():
    return 50 + random.randint(0, 10)

def create_mini_bar(percentage, length=20):
    filled = int((percentage / 100) * length)
    bar = '█' * filled + '░' * (length - filled)
    return f"║ [{bar}]"

def is_bot_running_check(script_key):
    return safe_is_bot_running_check(script_key)

def cleanup_script(script_key):
    if script_key in bot_scripts:
        script_info = bot_scripts[script_key]
        if 'log_file' in script_info and hasattr(script_info['log_file'], 'close'):
            try:
                if not script_info['log_file'].closed:
                    script_info['log_file'].close()
            except:
                pass
        del bot_scripts[script_key]
        logger.info(f"Cleaned up script: {script_key}")

# ─────────────────────────────────────────────
#  MONGODB — COIN SYSTEM
# ─────────────────────────────────────────────
def get_user_coins(user_id):
    if user_id not in user_coins:
        doc = col_users.find_one({"user_id": user_id}, {"coins": 1})
        user_coins[user_id] = doc["coins"] if doc and "coins" in doc else 0
    return user_coins.get(user_id, 0)

def add_coins(user_id, amount, transaction_type="", description=""):
    if user_id not in user_coins:
        user_coins[user_id] = 0
    user_coins[user_id] = max(0, user_coins[user_id] + amount)

    col_users.update_one(
        {"user_id": user_id},
        {"$inc": {"coins": amount, "total_earned": amount if amount > 0 else 0}},
        upsert=True
    )
    if transaction_type:
        col_transactions.insert_one({
            "user_id": user_id, "amount": amount,
            "transaction_type": transaction_type,
            "description": description,
            "timestamp": datetime.now().isoformat()
        })
    return user_coins[user_id]

def deduct_coins(user_id, amount, transaction_type="", description=""):
    if get_user_coins(user_id) < amount:
        return False, "Insufficient coins"
    user_coins[user_id] -= amount
    col_users.update_one({"user_id": user_id}, {"$inc": {"coins": -amount}})
    if transaction_type:
        col_transactions.insert_one({
            "user_id": user_id, "amount": -amount,
            "transaction_type": transaction_type,
            "description": description,
            "timestamp": datetime.now().isoformat()
        })
    return True, "Success"

def check_daily_bonus(user_id):
    doc = col_users.find_one({"user_id": user_id}, {"last_daily_bonus": 1})
    if doc and doc.get("last_daily_bonus"):
        last = datetime.fromisoformat(doc["last_daily_bonus"])
        if datetime.now().date() <= last.date():
            return False
    add_coins(user_id, COIN_PER_DAY_ACTIVE, "daily_bonus", "Daily Activity Bonus")
    col_users.update_one({"user_id": user_id},
                         {"$set": {"last_daily_bonus": datetime.now().isoformat()}},
                         upsert=True)
    return True

def generate_referral_code(user_id):
    if user_id in user_referrals:
        return user_referrals[user_id]
    doc = col_referrals.find_one({"user_id": user_id})
    if doc:
        user_referrals[user_id] = doc["referral_code"]
        return doc["referral_code"]
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    code = f"{user_id}_{random_part}"
    col_referrals.insert_one({"user_id": user_id, "referral_code": code,
                               "created_at": datetime.now().isoformat()})
    user_referrals[user_id] = code
    return code

def process_referral(referred_id, referral_code):
    ref_doc = col_referrals.find_one({"referral_code": referral_code})
    if not ref_doc:
        return False
    referrer_id = ref_doc["user_id"]
    existing = col_ref_rels.find_one({"referred_id": referred_id})
    if existing:
        return False
    col_ref_rels.insert_one({"referrer_id": referrer_id, "referred_id": referred_id,
                              "referred_at": datetime.now().isoformat()})
    user_referrers[referred_id] = referrer_id
    if referrer_id not in referral_stats:
        referral_stats[referrer_id] = []
    referral_stats[referrer_id].append(referred_id)
    add_coins(referrer_id, COIN_PER_REFERRAL, "referral_bonus", f"Referred user {referred_id}")
    add_coins(referred_id, COIN_PER_REFERRED_USER, "referred_bonus", f"Referred by {referrer_id}")
    return True

def get_referral_stats(user_id):
    refs = [d["referred_id"] for d in col_ref_rels.find({"referrer_id": user_id})]
    total = len(refs)
    return {'total': total, 'earned_coins': total * COIN_PER_REFERRAL, 'list': refs[:10]}

def generate_redeem_code(coin_value, code_type="coin", days_valid=30, created_by=None):
    created_by = created_by or OWNER_ID
    code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
    expires_at = datetime.now() + timedelta(days=days_valid)
    col_redeem_codes.insert_one({
        "code": code, "coin_value": coin_value, "code_type": code_type,
        "created_by": created_by, "created_at": datetime.now().isoformat(),
        "expires_at": expires_at.isoformat(), "used_by": None, "used_at": None
    })
    redeem_codes[code] = {'value': coin_value, 'type': code_type, 'expires': expires_at}
    return code

def redeem_code(user_id, code):
    doc = col_redeem_codes.find_one({"code": code})
    if not doc:
        return False, "Invalid code"
    if doc.get("used_by"):
        return False, "Code already used"
    if doc.get("expires_at"):
        if datetime.now() > datetime.fromisoformat(doc["expires_at"]):
            return False, "Code expired"
    col_redeem_codes.update_one({"code": code},
                                 {"$set": {"used_by": user_id, "used_at": datetime.now().isoformat()}})
    if code in redeem_codes:
        del redeem_codes[code]
    value     = doc["coin_value"]
    code_type = doc["code_type"]
    if code_type == "coin":
        add_coins(user_id, value, "code_redeem", f"Redeemed code: {code}")
        return True, f"🎉 Success! {value} coins added to your account!"
    elif code_type == "premium":
        expiry = datetime.now() + timedelta(days=7)
        user_subscriptions[user_id] = {'expiry': expiry}
        save_subscription(user_id, expiry)
        return True, "🎉 Success! Premium activated for 7 days!"
    elif code_type == "file_slot":
        return True, "🎉 Success! File slot added!"
    return False, "Unknown code type"

def get_top_referrers(limit=10):
    pipeline = [
        {"$group": {"_id": "$referrer_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit}
    ]
    results = list(col_ref_rels.aggregate(pipeline))
    return [{"user_id": r["_id"], "referrals": r["count"],
             "earned_coins": r["count"] * COIN_PER_REFERRAL} for r in results]

# ─────────────────────────────────────────────
#  MONGODB — GENERAL DB FUNCTIONS
# ─────────────────────────────────────────────
def save_subscription(user_id, expiry):
    col_subscriptions.update_one(
        {"user_id": user_id},
        {"$set": {"expiry": expiry.isoformat()}},
        upsert=True
    )

def save_active_user(user_id, username=None):
    now = datetime.now().isoformat()
    col_users.update_one(
        {"user_id": user_id},
        {"$set": {"last_seen": now, "username": username},
         "$setOnInsert": {"first_seen": now}},
        upsert=True
    )

def log_action(user_id, action, details):
    col_bot_logs.insert_one({
        "user_id": user_id, "action": action, "details": details,
        "timestamp": datetime.now().isoformat()
    })

def save_user_file_db(user_id, file_name, file_type, file_size=0):
    col_user_files.update_one(
        {"user_id": user_id, "file_name": file_name},
        {"$set": {"file_type": file_type, "file_size": file_size,
                  "upload_time": datetime.now().isoformat()}},
        upsert=True
    )
    add_coins(user_id, COIN_PER_FILE_UPLOAD, "file_upload", f"Uploaded {file_name}")
    log_action(user_id, "FILE_UPLOAD", f"Uploaded {file_name}")

def remove_user_file_db(user_id, file_name):
    col_user_files.delete_one({"user_id": user_id, "file_name": file_name})
    log_action(user_id, "FILE_DELETE", f"Deleted {file_name}")

def load_data():
    logger.info("Loading data from MongoDB...")
    # Subscriptions
    for doc in col_subscriptions.find():
        try:
            user_subscriptions[doc["user_id"]] = {'expiry': datetime.fromisoformat(doc["expiry"])}
        except:
            pass
    # User files
    for doc in col_user_files.find():
        uid = doc["user_id"]
        if uid not in user_files:
            user_files[uid] = []
        user_files[uid].append((doc["file_name"], doc["file_type"]))
    # Active users
    for doc in col_users.find({}, {"user_id": 1}):
        active_users.add(doc["user_id"])
    # Coins
    for doc in col_users.find({}, {"user_id": 1, "coins": 1}):
        user_coins[doc["user_id"]] = doc.get("coins", 0)
    # Referral codes
    for doc in col_referrals.find():
        user_referrals[doc["user_id"]] = doc["referral_code"]
    # Referral relationships
    for doc in col_ref_rels.find():
        referrer_id = doc["referrer_id"]
        referred_id = doc["referred_id"]
        user_referrers[referred_id] = referrer_id
        if referrer_id not in referral_stats:
            referral_stats[referrer_id] = []
        referral_stats[referrer_id].append(referred_id)
    # Active redeem codes
    for doc in col_redeem_codes.find({"used_by": None}):
        redeem_codes[doc["code"]] = {
            'value': doc["coin_value"], 'type': doc["code_type"],
            'expires': datetime.fromisoformat(doc["expires_at"]) if doc.get("expires_at") else None
        }
    # Admins from DB
    col_admins.update_one({"user_id": OWNER_ID}, {"$set": {"user_id": OWNER_ID, "added_by": OWNER_ID,
                           "added_at": datetime.now().isoformat()}}, upsert=True)
    if ADMIN_ID != OWNER_ID:
        col_admins.update_one({"user_id": ADMIN_ID}, {"$set": {"user_id": ADMIN_ID, "added_by": OWNER_ID,
                               "added_at": datetime.now().isoformat()}}, upsert=True)
    for doc in col_admins.find():
        admin_ids.add(doc["user_id"])
    logger.info(f"Loaded: {len(active_users)} users, {len(user_subscriptions)} subs, "
                f"{len(user_coins)} coin records, {len(admin_ids)} admins")

# ─────────────────────────────────────────────
#  USER HELPERS
# ─────────────────────────────────────────────
def get_user_folder(user_id):
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    base = FREE_USER_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        base = SUBSCRIBED_USER_LIMIT
    # Purchased slots
    purchased = col_transactions.count_documents({
        "user_id": user_id,
        "transaction_type": "file_slot_purchase",
        "amount": {"$lt": 0}
    })
    return base + purchased

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        is_running = safe_is_bot_running_check(script_key)
        if not is_running:
            cleanup_script(script_key)
        return is_running
    return False

def kill_process_tree(process_info):
    safe_kill_process_tree(process_info)

# ─────────────────────────────────────────────
#  STATS MESSAGES
# ─────────────────────────────────────────────
def get_system_stats():
    stats = safe_get_system_stats()
    total_coins = sum(user_coins.values())
    return {**stats, 'uptime': get_uptime(),
            'response_time': get_optimized_response_time(),
            'total_coins': total_coins,
            'total_users_with_coins': len(user_coins)}

def create_system_stats_message():
    stats = get_system_stats()
    running_bots = len([k for k in bot_scripts if safe_is_bot_running_check(k)])
    if stats.get('using_psutil'):
        cpu_text  = f"{stats['cpu']}%"
        mem_text  = f"{stats['memory_used']}% / {stats['memory_total']}"
        disk_text = f"{stats['disk_used']}% / {stats['disk_total']}"
    else:
        cpu_text = mem_text = disk_text = "N/A"
    return f"""
╔══════════════════════════════════════╗
║       📊 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓 𝐒𝐓𝐀𝐓𝐒</b> 📊         ║
╠══════════════════════════════════════╣
║ 🖥️ <b>𝐂𝐏𝐔:</b> {cpu_text}
║ 🧠 <b>𝐌𝐞𝐦𝐨𝐫𝐲:</b> {mem_text}
║ 💾 <b>𝐃𝐢𝐬𝐤:</b> {disk_text}
║ ⚡ <b>𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞:</b> {stats['response_time']:.1f}ms
║ ⏱️ <b>𝐔𝐩𝐭𝐢𝐦𝐞:</b> {stats['uptime']}
║ 🤖 <b>𝐑𝐮𝐧𝐧𝐢𝐧𝐠:</b> {running_bots} bots
║ 👥 <b>𝐔𝐬𝐞𝐫𝐬:</b> {len(active_users)}
║ 💰 <b>𝐓𝐨𝐭𝐚𝐥 𝐂𝐨𝐢𝐧𝐬:</b> {stats['total_coins']:,}
╚══════════════════════════════════════╝
"""

def create_user_stats_message(user_id):
    files_count = get_user_file_count(user_id)
    limit       = get_user_file_limit(user_id)
    coins       = get_user_coins(user_id)
    ref_stats   = get_referral_stats(user_id)
    is_premium  = (user_id in user_subscriptions and
                   user_subscriptions[user_id]['expiry'] > datetime.now())
    expiry      = user_subscriptions[user_id]['expiry'] if is_premium else None
    user_running = sum(1 for k, v in bot_scripts.items()
                       if safe_is_bot_running_check(k) and v.get('user_id') == user_id)
    doc = col_users.find_one({"user_id": user_id}, {"last_daily_bonus": 1})
    last_daily = "Never"
    if doc and doc.get("last_daily_bonus"):
        last_daily = datetime.fromisoformat(doc["last_daily_bonus"]).strftime('%Y-%m-%d %H:%M')
    if user_id == OWNER_ID:
        status = "👑 Owner"
    elif user_id in admin_ids:
        status = "⭐ Admin"
    elif is_premium:
        status = "🌟 Premium"
    else:
        status = "👤 Free"
    msg = f"""
╔══════════════════════════════════════╗
║    📊 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓: MY STATS</b> 📊      ║
╠══════════════════════════════════════╣
║ 👤 𝐈𝐃: {user_id}
║ 🏷️ 𝐒𝐭𝐚𝐭𝐮𝐬: {status}
║ 📅 𝐋𝐚𝐬𝐭 𝐁𝐨𝐧𝐮𝐬: {last_daily}
║
║ 📁 𝐅𝐢𝐥𝐞𝐬: {files_count}/{int(limit) if limit != float('inf') else '∞'}
║ 🤖 𝐑𝐮𝐧𝐧𝐢𝐧𝐠: {user_running}
║
║ 💰 𝐁𝐚𝐥𝐚𝐧𝐜𝐞: {coins:,} coins
║ 👥 𝐑𝐞𝐟𝐞𝐫𝐫𝐚𝐥𝐬: {ref_stats['total']} (+{ref_stats['earned_coins']:,})
"""
    if is_premium and expiry:
        days_left = (expiry - datetime.now()).days
        msg += f"║ ⭐ 𝐏𝐫𝐞𝐦𝐢𝐮𝐦: {days_left}d left ({expiry.strftime('%Y-%m-%d')})\n"
    msg += f"""║
║ 📤 Upload: +{COIN_PER_FILE_UPLOAD}/file
║ 🎁 Refer:  +{COIN_PER_REFERRAL}/user
║ 📅 Daily:  +{COIN_PER_DAY_ACTIVE}/day
╚══════════════════════════════════════╝
"""
    return msg

# ─────────────────────────────────────────────
#  KEYBOARDS (colorful button styles)
# ─────────────────────────────────────────────
def get_main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id == OWNER_ID or user_id in admin_ids:
        markup.row("🟢 Upload File", "🗂️ My Files")
        markup.row("🪙 My Coins", "🎁 Refer & Earn")
        markup.row("⚡ Bot Speed", "📊 My Stats")
        markup.row("💳 Subscriptions", "🔑 Redeem Code")
        markup.row("📢 Broadcast", "👑 Admin Panel")
        markup.row("🔐 Lock Bot", "📞 Contact")
    else:
        markup.row("🟢 Upload File", "🗂️ My Files")
        markup.row("🪙 My Coins", "🎁 Refer & Earn")
        markup.row("⚡ Bot Speed", "📊 My Stats")
        markup.row("🔑 Redeem Code", "📞 Contact")
    return markup

def get_coin_keyboard(user_id):
    coins = get_user_coins(user_id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(f"💰 Balance: {coins:,}", callback_data="coin_balance"),
        types.InlineKeyboardButton("🏆 Leaderboard", callback_data="coin_leaderboard")
    )
    markup.add(
        types.InlineKeyboardButton("🛒 File Slot (100 🪙)", callback_data="buy_file_slot"),
        types.InlineKeyboardButton("⭐ Premium (500 🪙)", callback_data="buy_premium")
    )
    markup.add(
        types.InlineKeyboardButton("📜 Transactions", callback_data="coin_transactions"),
        types.InlineKeyboardButton("🎁 Refer & Earn", callback_data="refer_earn")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))
    return markup

def get_referral_keyboard(user_id):
    code  = generate_referral_code(user_id)
    stats = get_referral_stats(user_id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📋 My Referrals", callback_data="my_referrals"),
        types.InlineKeyboardButton("🏆 Leaderboard", callback_data="referral_leaderboard")
    )
    markup.add(types.InlineKeyboardButton("📤 Share My Code",
                                           switch_inline_query=f"Join using my code: {code}"))
    markup.add(
        types.InlineKeyboardButton(f"💸 Earned: {stats['earned_coins']} 🪙",
                                   callback_data="earned_coins"),
        types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
    )
    return markup

def get_file_actions_keyboard(file_name, is_running=False):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.add(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f"stop_{file_name}"),
            types.InlineKeyboardButton("📋 Logs", callback_data=f"logs_{file_name}")
        )
        markup.add(types.InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{file_name}"))
    else:
        markup.add(
            types.InlineKeyboardButton("🟢 Run", callback_data=f"run_{file_name}"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{file_name}")
        )
        markup.add(
            types.InlineKeyboardButton("📥 Download", callback_data=f"download_{file_name}"),
            types.InlineKeyboardButton("📝 Edit", callback_data=f"edit_{file_name}")
        )
        markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data="back_to_files"))
    return markup

# ─────────────────────────────────────────────
#  ANIMATION
# ─────────────────────────────────────────────
def send_animated_message(chat_id, final_text, animation_type="loading", duration=2, steps=4):
    try:
        action_map = {
            "loading": "Authenticating session", "upload": "Uploading file",
            "download": "Downloading file", "delete": "Deleting file",
            "run": "Starting script", "stop": "Stopping script",
            "install": "Installing dependencies", "terminal": "Initializing terminal"
        }
        action_text = action_map.get(animation_type, "Processing")
        msg = None
        for i in range(steps + 1):
            percent = int((i / steps) * 100)
            bar     = "🟩" * i + "⬜" * (steps - i)
            display = f"⚙️ 𝐋ᴏᴀᴅɪɴɢ... ({percent}%)\n[{bar}] {action_text}..."
            if i == 0:
                msg = bot.send_message(chat_id, display)
            else:
                try:
                    bot.edit_message_text(display, chat_id, msg.message_id)
                except:
                    pass
            time.sleep(duration / steps)
        try:
            bot.edit_message_text(final_text, chat_id, msg.message_id, parse_mode='HTML')
        except:
            bot.send_message(chat_id, final_text, parse_mode='HTML')
        return msg
    except Exception as e:
        logger.error(f"Animation error: {e}")
        return bot.send_message(chat_id, final_text, parse_mode='HTML')

# ─────────────────────────────────────────────
#  COMMAND HANDLERS
# ─────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id  = message.from_user.id
    username = message.from_user.username or "Unknown"
    active_users.add(user_id)
    save_active_user(user_id, username)

    parts = message.text.split()
    if len(parts) > 1:
        referral_code = parts[1]
        if referral_code != str(user_id):
            if process_referral(user_id, referral_code):
                logger.info(f"User {user_id} joined via referral {referral_code}")

    daily_bonus_given = check_daily_bonus(user_id)
    generate_referral_code(user_id)

    if bot_locked and user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, "🔐 Bot is currently locked.")
        return

    coins         = get_user_coins(user_id)
    response_time = get_optimized_response_time()
    limit         = get_user_file_limit(user_id)

    welcome_text = f"""
╔══════════════════════════════════════╗
║    🤖 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓 🦁</b>  |  {YOUR_USERNAME}  ║
╠══════════════════════════════════════╣
║
║  👋 Welcome, <b>{message.from_user.first_name}</b>!
║  ⚡ <b>Response:</b> {response_time:.1f}ms
║  💰 <b>Coins:</b> {coins:,}
║
║  🟢 Upload &amp; Host your bot files
║  🎁 <b>Earn Coins</b> — Refer &amp; Upload!
║  🔑 Redeem codes for premium
║
╠══════════════════════════════════════╣
║  📁 Files: {get_user_file_count(user_id)}/{int(limit) if limit != float('inf') else '∞'}
║  💳 Status: {'👑 Owner' if user_id == OWNER_ID else '⭐ Admin' if user_id in admin_ids else '🌟 Premium' if user_id in user_subscriptions else '👤 Free'}
"""
    if daily_bonus_given:
        welcome_text += f"║  🎉 <b>Daily Bonus:</b> +{COIN_PER_DAY_ACTIVE} coins!\n"
    welcome_text += "╚══════════════════════════════════════╝\n⬇️ Choose an option:"

    bot.send_message(message.chat.id, welcome_text, parse_mode='HTML')
    bot.send_message(message.chat.id, "📌 Main Menu:", reply_markup=get_main_keyboard(user_id))
    log_action(user_id, "START", f"Joined with {coins} coins")

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = """
╔══════════════════════════════════════╗
║       📚 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓 HELP</b> 📚          ║
╠══════════════════════════════════════╣
║ <b>📁 Files:</b> /upload /files /delete
║ <b>💰 Coins:</b> /coins /refer /redeem
║ <b>🏆 Rank:</b>  /leaderboard
║ <b>🤖 Bots:</b>  /run /stop /logs /running
║ <b>📊 Info:</b>  /stats /speed /status
║ <b>🔧 Admin:</b> /broadcast /subscribe /lock
║          /createcode /givecoins
║ <b>👑 Owner:</b> /admin addadmin &lt;id&gt;
║          /admin removeadmin &lt;id&gt;
║          /admin listadmins
╚══════════════════════════════════════╝
"""
    bot.send_message(message.chat.id, help_text, parse_mode='HTML')

@bot.message_handler(commands=['stats', 'statistics'])
def stats_command(message):
    user_id = message.from_user.id
    if user_id == OWNER_ID or user_id in admin_ids:
        bot.send_message(message.chat.id, create_system_stats_message(), parse_mode='HTML')
    else:
        mystats_command(message)

@bot.message_handler(commands=['mystats'])
def mystats_command(message):
    bot.send_message(message.chat.id,
                     create_user_stats_message(message.from_user.id), parse_mode='HTML')

@bot.message_handler(commands=['speed'])
def speed_command(message):
    start      = time.time()
    rt         = get_optimized_response_time()
    proc_time  = (time.time() - start) * 1000
    s = safe_get_system_stats()
    status_emoji = "🟢" if rt < 60 else ("🟡" if rt < 100 else "🔴")
    bot.send_message(message.chat.id, f"""
╔══════════════════════════════════════╗
║        ⚡ <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓 𝐒𝐏𝐄𝐄𝐃</b> ⚡        ║
╠══════════════════════════════════════╣
║ 🏓 Response: {rt:.1f}ms
║ ⚡ Processing: {proc_time:.1f}ms
║ 🖥️ CPU: {s['cpu']}%
║ 🧠 Memory: {s['memory_used']}%
║ ⏱️ Uptime: {get_uptime()}
║ {status_emoji} Status: {'Excellent' if rt < 60 else ('Good' if rt < 100 else 'Slow')}
╚══════════════════════════════════════╝
""", parse_mode='HTML')

@bot.message_handler(commands=['running'])
def running_command(message):
    user_id      = message.from_user.id
    running_bots = []
    for script_key, info in bot_scripts.items():
        if safe_is_bot_running_check(script_key):
            if user_id == OWNER_ID or user_id in admin_ids or info.get('user_id') == user_id:
                uptime = datetime.now() - info.get('start_time', datetime.now())
                running_bots.append({
                    'file': info.get('file_name', 'Unknown'),
                    'user': info.get('user_id', 'Unknown'),
                    'pid': info.get('process').pid if info.get('process') else 'N/A',
                    'uptime': str(uptime).split('.')[0]
                })
    if running_bots:
        text = "╔══════════════════════════════════════╗\n║ 🟢 <b>Running Bots</b>\n╠══════════════════════════════════════╣\n"
        for i, b in enumerate(running_bots, 1):
            text += f"║ {i}. <code>{b['file'][:20]}</code> | 🆔{b['pid']} | ⏱{b['uptime']}\n"
        text += "╚══════════════════════════════════════╝"
    else:
        text = "╔══════════════════════════════════════╗\n║ 🔴 <b>No running bots</b>\n╚══════════════════════════════════════╝"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['lock'])
def lock_command(message):
    global bot_locked
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Permission denied!")
        return
    bot_locked = not bot_locked
    status = "🔐 LOCKED" if bot_locked else "🔓 UNLOCKED"
    bot.send_message(message.chat.id, f"""
╔══════════════════════════════════════╗
║         🔐 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓 𝐒𝐓𝐀𝐓𝐒</b> 🔐       ║
╠══════════════════════════════════════╣
║ Status: {status}
║ By: {message.from_user.first_name}
║ Time: {datetime.now().strftime('%H:%M:%S')}
╚══════════════════════════════════════╝
""", parse_mode='HTML')

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Permission denied!")
        return
    msg = bot.reply_to(message, "📢 Send the message to broadcast:")
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    broadcast_text = message.text
    if not broadcast_text:
        bot.reply_to(message, "❌ Send a text message!")
        return
    success = failed = 0
    for uid in active_users:
        try:
            bot.send_message(uid, f"📢 <b>𝐆𝐇𝐎𝐒𝐓 𝐂𝐎𝐃𝐄𝐑 | {YOUR_USERNAME}</b>\n\n{broadcast_text}", parse_mode='HTML')
            success += 1
        except:
            failed += 1
    bot.send_message(message.chat.id, f"✅ Broadcast done!\n📤 Total: {success+failed}\n✅ Sent: {success}\n❌ Failed: {failed}")

@bot.message_handler(commands=['subscribe', 'sub'])
def subscribe_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Permission denied!")
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /subscribe <user_id> <days>")
        return
    try:
        target_user = int(parts[1])
        days        = int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID or days!")
        return
    expiry = datetime.now() + timedelta(days=days)
    user_subscriptions[target_user] = {'expiry': expiry}
    save_subscription(target_user, expiry)
    bot.send_message(message.chat.id,
                     f"✅ Subscribed user <code>{target_user}</code> for {days} days!\n"
                     f"⏰ Expires: {expiry.strftime('%Y-%m-%d %H:%M')}",
                     parse_mode='HTML')
    try:
        bot.send_message(target_user,
                         f"🎉 You've been subscribed for {days} days by Ghost Coder!\n"
                         f"Contact: {YOUR_USERNAME}")
    except:
        pass

# ─────────────────────────────────────────────
#  COIN COMMANDS
# ─────────────────────────────────────────────
@bot.message_handler(commands=['coins', 'balance'])
def coins_command(message):
    user_id = message.from_user.id
    coins   = get_user_coins(user_id)
    bot.send_message(message.chat.id, f"""
╔══════════════════════════════════════╗
║      💰 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓: COINS</b> 💰        ║
╠══════════════════════════════════════╣
║ 👤 {message.from_user.first_name}  |  🆔 {user_id}
║ 💰 Balance: <b>{coins:,} coins</b>
║
║ 🟢 Earn: Upload (+{COIN_PER_FILE_UPLOAD}) Refer (+{COIN_PER_REFERRAL}) Daily (+{COIN_PER_DAY_ACTIVE})
║ 🛒 Spend: File Slot ({COIN_REDEEM_FILE_COST}) Premium ({COIN_REDEEM_PREMIUM_COST})
╚══════════════════════════════════════╝
""", parse_mode='HTML', reply_markup=get_coin_keyboard(user_id))

@bot.message_handler(commands=['refer', 'referral'])
def refer_command(message):
    user_id = message.from_user.id
    code    = generate_referral_code(user_id)
    stats   = get_referral_stats(user_id)
    me      = bot.get_me()
    bot.send_message(message.chat.id, f"""
╔══════════════════════════════════════╗
║     🎁 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓: REFER & EARN</b> 🎁   ║
╠══════════════════════════════════════╣
║ 🔑 <b>Your Code:</b> <code>{code}</code>
║ 👥 Referrals: {stats['total']}
║ 💰 Earned: {stats['earned_coins']:,} coins
║
║ You get: +{COIN_PER_REFERRAL} 🪙  |  Friend gets: +{COIN_PER_REFERRED_USER} 🪙
║
║ 🔗 <b>Link:</b>
║ https://t.me/{me.username}?start={code}
╚══════════════════════════════════════╝
""", parse_mode='HTML', reply_markup=get_referral_keyboard(user_id))

@bot.message_handler(commands=['redeem'])
def redeem_command(message):
    user_id = message.from_user.id
    if len(message.text.split()) > 1:
        code        = message.text.split()[1].upper()
        success, msg= redeem_code(user_id, code)
        if success:
            bot.send_message(message.chat.id,
                             f"✅ <b>REDEEM SUCCESS</b>\n\n{msg}\n💰 New balance: {get_user_coins(user_id):,}",
                             parse_mode='HTML')
        else:
            bot.send_message(message.chat.id,
                             f"❌ <b>REDEEM FAILED</b>\n\n{msg}", parse_mode='HTML')
    else:
        bot.send_message(message.chat.id,
                         f"🔑 <b>Redeem Code</b>\n\nUsage: <code>/redeem YOUR_CODE</code>\n\n"
                         f"💰 Your coins: {get_user_coins(user_id):,}",
                         parse_mode='HTML')

@bot.message_handler(commands=['leaderboard'])
def leaderboard_command(message):
    top_refs  = get_top_referrers(10)
    top_coins = list(col_users.find({}, {"user_id": 1, "coins": 1})
                     .sort("coins", DESCENDING).limit(5))
    text = "╔══════════════════════════════════════╗\n║ 🏆 <b>LEADERBOARD</b>\n╠══════════════════════════════════════╣\n║ 👥 <b>Top Referrers</b>\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top_refs, 1):
        m = medals[i-1] if i <= 3 else f"{i}."
        text += f"║ {m} {u['user_id']} — {u['referrals']} refs | {u['earned_coins']:,} 🪙\n"
    text += "║\n║ 💰 <b>Top Coin Holders</b>\n"
    for i, doc in enumerate(top_coins, 1):
        m = medals[i-1] if i <= 3 else f"{i}."
        text += f"║ {m} {doc['user_id']} — {doc.get('coins', 0):,} 🪙\n"
    text += "╚══════════════════════════════════════╝"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

# ─────────────────────────────────────────────
#  ADMIN COIN COMMANDS
# ─────────────────────────────────────────────
@bot.message_handler(commands=['createcode'])
def createcode_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage:\n/createcode coin 100\n/createcode premium 1\n/createcode file_slot 1\n/createcode coin 500 60",
                     parse_mode='HTML')
        return
    code_type = parts[1].lower()
    try:
        value = int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ Invalid value!")
        return
    days = int(parts[3]) if len(parts) > 3 else 30
    code = generate_redeem_code(value, code_type, days, user_id)
    bot.reply_to(message, f"✅ <b>Code Created!</b>\n\n🔑 <code>{code}</code>\n🎁 Type: {code_type}\n💰 Value: {value}\n📅 Valid: {days} days\n\nUsage: /redeem {code}",
                 parse_mode='HTML')

@bot.message_handler(commands=['listcodes'])
def listcodes_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    if not redeem_codes:
        bot.reply_to(message, "📝 No active codes.")
        return
    text = "📋 <b>Active Redeem Codes:</b>\n\n"
    for code, data in list(redeem_codes.items())[:20]:
        expires = data['expires'].strftime('%Y-%m-%d') if data['expires'] else "Never"
        text += f"• <code>{code}</code> | {data['type']} | {data['value']} 🪙 | {expires}\n"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

@bot.message_handler(commands=['givecoins'])
def givecoins_command(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /givecoins <user_id> <amount>")
        return
    try:
        target_id = int(parts[1])
        amount    = int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID or amount!")
        return
    if amount <= 0:
        bot.reply_to(message, "❌ Amount must be positive!")
        return
    new_balance = add_coins(target_id, amount, "admin_gift", f"Gifted by admin {user_id}")
    bot.reply_to(message, f"🎁 Gifted {amount:,} 🪙 to <code>{target_id}</code>\n📊 New balance: {new_balance:,}",
                 parse_mode='HTML')
    try:
        bot.send_message(target_id, f"🎁 You received {amount:,} coins from admin!\n💰 New balance: {new_balance:,}")
    except:
        pass

# ─────────────────────────────────────────────
#  ADMIN MANAGEMENT COMMANDS
# ─────────────────────────────────────────────
@bot.message_handler(commands=['admin'])
def admin_command(message):
    """Admin management — only Owner can use"""
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        bot.reply_to(message, "❌ Only Owner can manage admins!")
        return

    parts = message.text.split()
    if len(parts) < 2:
        _send_admin_help(message)
        return

    sub = parts[1].lower()

    if sub == "addadmin":
        if len(parts) < 3:
            bot.reply_to(message, "Usage: /admin addadmin <user_id>")
            return
        try:
            target_id = int(parts[2])
        except ValueError:
            bot.reply_to(message, "❌ Invalid user ID!")
            return
        if target_id == OWNER_ID:
            bot.reply_to(message, "⚠️ Owner is already the top admin!")
            return
        if target_id in admin_ids:
            bot.reply_to(message, f"⚠️ <code>{target_id}</code> is already an admin!", parse_mode='HTML')
            return
        col_admins.update_one(
            {"user_id": target_id},
            {"$set": {"user_id": target_id, "added_by": user_id,
                      "added_at": datetime.now().isoformat()}},
            upsert=True
        )
        admin_ids.add(target_id)
        log_action(user_id, "ADD_ADMIN", f"Added admin {target_id}")
        bot.reply_to(message, f"""
╔══════════════════════════════════════╗
║      ✅ <b>ADMIN ADDED</b> ✅               ║
╠══════════════════════════════════════╣
║ 👤 User ID: <code>{target_id}</code>
║ ⭐ Status: Admin
║ 👑 Added by: {message.from_user.first_name}
║ 🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}
╚══════════════════════════════════════╝
""", parse_mode='HTML')
        try:
            bot.send_message(target_id,
                             f"⭐ You've been made an Admin by {YOUR_USERNAME}!\n"
                             f"Use /help to see admin commands.")
        except:
            pass

    elif sub == "removeadmin":
        if len(parts) < 3:
            bot.reply_to(message, "Usage: /admin removeadmin <user_id>")
            return
        try:
            target_id = int(parts[2])
        except ValueError:
            bot.reply_to(message, "❌ Invalid user ID!")
            return
        if target_id == OWNER_ID:
            bot.reply_to(message, "❌ Cannot remove Owner!")
            return
        if target_id not in admin_ids:
            bot.reply_to(message, f"⚠️ <code>{target_id}</code> is not an admin!", parse_mode='HTML')
            return
        col_admins.delete_one({"user_id": target_id})
        admin_ids.discard(target_id)
        log_action(user_id, "REMOVE_ADMIN", f"Removed admin {target_id}")
        bot.reply_to(message, f"""
╔══════════════════════════════════════╗
║      🔴 <b>ADMIN REMOVED</b> 🔴             ║
╠══════════════════════════════════════╣
║ 👤 User ID: <code>{target_id}</code>
║ 🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}
╚══════════════════════════════════════╝
""", parse_mode='HTML')
        try:
            bot.send_message(target_id, "⚠️ Your admin access has been revoked.")
        except:
            pass

    elif sub == "listadmins":
        _list_admins(message)

    elif sub == "help":
        _send_admin_help(message)

    else:
        _send_admin_help(message)

def _send_admin_help(message):
    bot.reply_to(message, f"""
╔══════════════════════════════════════╗
║     👑 <b>ADMIN MANAGEMENT</b> 👑          ║
╠══════════════════════════════════════╣
║ ➕ <b>Add Admin:</b>
║   <code>/admin addadmin &lt;user_id&gt;</code>
║
║ ➖ <b>Remove Admin:</b>
║   <code>/admin removeadmin &lt;user_id&gt;</code>
║
║ 📋 <b>List Admins:</b>
║   <code>/admin listadmins</code>
║
║ 👤 <b>Total Admins:</b> {len(admin_ids)}
╚══════════════════════════════════════╝
""", parse_mode='HTML')

def _list_admins(message):
    docs = list(col_admins.find())
    if not docs:
        bot.reply_to(message, "📋 No admins found.")
        return
    text = "╔══════════════════════════════════════╗\n║ 📋 <b>ADMIN LIST</b>\n╠══════════════════════════════════════╣\n"
    for i, doc in enumerate(docs, 1):
        uid      = doc["user_id"]
        role     = "👑 Owner" if uid == OWNER_ID else "⭐ Admin"
        added_at = doc.get("added_at", "N/A")[:10]
        added_by = doc.get("added_by", "N/A")
        text += f"║ {i}. {role}\n║    🆔 <code>{uid}</code>\n║    📅 {added_at} | 👤 by {added_by}\n║\n"
    text += f"╠══════════════════════════════════════╣\n║ 👥 Total: {len(docs)} admins\n╚══════════════════════════════════════╝"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="admin_listadmins"))
    bot.reply_to(message, text, parse_mode='HTML', reply_markup=markup)

# ─────────────────────────────────────────────
#  TEXT MESSAGE HANDLER (buttons)
# ─────────────────────────────────────────────
@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    text    = message.text
    active_users.add(user_id)
    if bot_locked and user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, "🔐 Bot is locked!")
        return
    if text == "📢 Updates Channel":
        bot.send_message(message.chat.id, f"📢 Updates: {UPDATE_CHANNEL}")
    elif text == "🟢 Upload File":
        handle_upload_request(message)
    elif text in ("🗂️ My Files", "📂 My Files"):
        show_user_files(message)
    elif text == "🪙 My Coins":
        coins_command(message)
    elif text == "🎁 Refer & Earn":
        refer_command(message)
    elif text == "🔑 Redeem Code":
        redeem_command(message)
    elif text == "⚡ Bot Speed":
        speed_command(message)
    elif text == "📊 My Stats":
        mystats_command(message)
    elif text == "💳 Subscriptions":
        show_subscriptions(message)
    elif text == "📢 Broadcast":
        broadcast_command(message)
    elif text == "🔐 Lock Bot":
        lock_command(message)
    elif text == "👑 Admin Panel":
        show_admin_panel(message)
    elif text == "📞 Contact":
        bot.send_message(message.chat.id, f"📞 Contact: {YOUR_USERNAME}\n🔔 Updates: {UPDATE_CHANNEL}")
    elif text == "🏆 Leaderboard":
        leaderboard_command(message)

def handle_upload_request(message):
    user_id = message.from_user.id
    current = get_user_file_count(user_id)
    limit   = get_user_file_limit(user_id)
    if current >= limit:
        bot.reply_to(message,
                     f"❌ File limit reached ({current}/{int(limit) if limit != float('inf') else '∞'})\n\n"
                     f"Buy more slots with coins or upgrade to Premium!")
        return
    bot.send_message(message.chat.id, f"""
╔══════════════════════════════════════╗
║      🟢 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓: UPLOAD</b> 🟢        ║
╠══════════════════════════════════════╣
║ Send your file now!
║
║ ✅ .py  ✅ .js  ✅ .zip
║ ✅ .json  ✅ .txt  ✅ .env
║
║ 📁 Files: {current}/{int(limit) if limit != float('inf') else '∞'}
║ 💰 Earn: +{COIN_PER_FILE_UPLOAD} coins per upload!
╚══════════════════════════════════════╝
""", parse_mode='HTML')

def show_user_files(message):
    user_id = message.from_user.id
    files   = user_files.get(user_id, [])
    if not files:
        bot.send_message(message.chat.id, f"""
╔══════════════════════════════════════╗
║      🗂️ <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓: MY FILES</b> 🗂️     ║
╠══════════════════════════════════════╣
║ No files uploaded yet!
║ Use 🟢 Upload File to start.
║ 💰 Earn +{COIN_PER_FILE_UPLOAD} coins per upload!
╚══════════════════════════════════════╝
""", parse_mode='HTML')
        return
    text = "╔══════════════════════════════════════╗\n║ 🗂️ <b>MY FILES</b>\n╠══════════════════════════════════════╣\n"
    markup = types.InlineKeyboardMarkup(row_width=2)
    for i, (file_name, file_type) in enumerate(files, 1):
        running   = is_bot_running(user_id, file_name)
        status    = "🟢" if running else "🔴"
        type_icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📦"
        text += f"║ {i}. {status} {type_icon} <code>{file_name[:25]}</code>\n"
        markup.add(types.InlineKeyboardButton(
            f"{status} {file_name[:15]}", callback_data=f"file_{file_name}"))
    text += f"╠══════════════════════════════════════╣\n║ 📁 Total: {len(files)} | 💰 Earned: {len(files)*COIN_PER_FILE_UPLOAD} 🪙\n╚══════════════════════════════════════╝\n\nSelect a file:"
    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=markup)

def show_subscriptions(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    active_subs = {uid: d for uid, d in user_subscriptions.items() if d['expiry'] > datetime.now()}
    text = f"💳 <b>Subscriptions</b>\nActive: {len(active_subs)} | Total: {len(user_subscriptions)}\n\n"
    for uid, d in list(active_subs.items())[:10]:
        remaining = d['expiry'] - datetime.now()
        text += f"👤 <code>{uid}</code>: {remaining.days}d left\n"
    text += f"\n<b>Add sub:</b> /subscribe &lt;id&gt; &lt;days&gt;\n<b>Premium cost:</b> {COIN_REDEEM_PREMIUM_COST} 🪙"
    bot.send_message(message.chat.id, text, parse_mode='HTML')

def show_admin_panel(message):
    user_id = message.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.reply_to(message, "❌ Admin only!")
        return
    total_coins = sum(user_coins.values())
    text = f"""
╔══════════════════════════════════════╗
║     👑 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓: ADMIN PANEL</b> 👑   ║
╠══════════════════════════════════════╣
║ 👥 Users: {len(active_users)}
║ 💳 Active Subs: {len([u for u, d in user_subscriptions.items() if d['expiry'] > datetime.now()])}
║ 🤖 Running Bots: {len([k for k in bot_scripts if safe_is_bot_running_check(k)])}
║ ⚡ Response: {get_optimized_response_time():.1f}ms
║
║ 💰 Total Coins: {total_coins:,}
║ 🪙 Users w/ Coins: {len(user_coins)}
║ 🔑 Active Codes: {len(redeem_codes)}
║
║ /broadcast /createcode /givecoins
║ /listcodes /subscribe /lock
╚══════════════════════════════════════╝
"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔴 Stop All Bots", callback_data="admin_stopall"),
        types.InlineKeyboardButton("💰 Coin Manager", callback_data="admin_coins")
    )
    markup.add(
        types.InlineKeyboardButton("🔑 Create Code", callback_data="admin_createcode"),
        types.InlineKeyboardButton("📊 Full Stats", callback_data="admin_fullstats")
    )
    markup.add(
        types.InlineKeyboardButton("📋 View Logs", callback_data="admin_logs"),
        types.InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh")
    )
    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=markup)

# ─────────────────────────────────────────────
#  DOCUMENT UPLOAD HANDLER
# ─────────────────────────────────────────────
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id  = message.from_user.id
    current  = get_user_file_count(user_id)
    limit    = get_user_file_limit(user_id)
    if current >= limit:
        bot.reply_to(message, f"❌ File limit reached! ({current}/{int(limit) if limit != float('inf') else '∞'})\n\n"
                              f"💰 Buy extra slots with coins!")
        return
    file_name = message.document.file_name
    file_size = message.document.file_size
    file_ext  = file_name.split('.')[-1].lower() if '.' in file_name else ''
    allowed   = ['py', 'js', 'zip', 'json', 'txt', 'env', 'yml', 'yaml']
    if file_ext not in allowed:
        bot.reply_to(message, f"❌ Unsupported type: .{file_ext}")
        return
    progress_msg = bot.reply_to(message, f"⏳ Uploading <code>{file_name[:25]}</code>...", parse_mode='HTML')
    try:
        file_info       = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        user_folder     = get_user_folder(user_id)
        if file_ext == 'zip':
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                tmp.write(downloaded_file)
                tmp_path = tmp.name
            try:
                with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                    zip_ref.extractall(user_folder)
                    extracted = []
                    for root, dirs, files_list in os.walk(user_folder):
                        for f in files_list:
                            if f.endswith(('.py', '.js')):
                                extracted.append(f)
                                if user_id not in user_files:
                                    user_files[user_id] = []
                                if (f, f.split('.')[-1]) not in user_files[user_id]:
                                    user_files[user_id].append((f, f.split('.')[-1]))
                                    save_user_file_db(user_id, f, f.split('.')[-1], 0)
                os.unlink(tmp_path)
                success_text = f"✅ ZIP extracted! {len(extracted)} files\n💰 Earned: +{len(extracted)*COIN_PER_FILE_UPLOAD} 🪙"
            except zipfile.BadZipFile:
                bot.edit_message_text("❌ Invalid ZIP file!", message.chat.id, progress_msg.message_id)
                return
        else:
            file_path = os.path.join(user_folder, file_name)
            with open(file_path, 'wb') as f:
                f.write(downloaded_file)
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id] = [(n, t) for n, t in user_files[user_id] if n != file_name]
            user_files[user_id].append((file_name, file_ext))
            save_user_file_db(user_id, file_name, file_ext, file_size)
            success_text = f"✅ <b>Upload Complete!</b>\n📄 <code>{file_name}</code>\n📦 {format_size(file_size)}\n💰 Earned: +{COIN_PER_FILE_UPLOAD} 🪙"
        markup = types.InlineKeyboardMarkup(row_width=2)
        if file_ext in ['py', 'js']:
            markup.add(
                types.InlineKeyboardButton("🟢 Run Now", callback_data=f"run_{file_name}"),
                types.InlineKeyboardButton("🗂️ My Files", callback_data="back_to_files")
            )
        else:
            markup.add(types.InlineKeyboardButton("🗂️ My Files", callback_data="back_to_files"))
        try:
            bot.edit_message_text(success_text, message.chat.id, progress_msg.message_id,
                                  parse_mode='HTML', reply_markup=markup)
        except:
            bot.send_message(message.chat.id, success_text, parse_mode='HTML', reply_markup=markup)
    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        try:
            bot.edit_message_text(f"❌ Upload failed: {str(e)[:100]}", message.chat.id, progress_msg.message_id)
        except:
            bot.reply_to(message, f"❌ Upload failed: {str(e)[:100]}")

# ─────────────────────────────────────────────
#  CALLBACK HANDLER
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data    = call.data
    try:
        if data.startswith("file_"):
            show_file_actions(call, data[5:])
        elif data.startswith("run_"):
            run_user_script(call, data[4:])
        elif data.startswith("stop_"):
            stop_user_script(call, data[5:])
        elif data.startswith("delete_"):
            delete_user_file(call, data[7:])
        elif data.startswith("download_"):
            download_user_file(call, data[9:])
        elif data.startswith("logs_"):
            show_script_logs(call, data[5:])
        elif data.startswith("restart_"):
            restart_user_script(call, data[8:])
        elif data.startswith("confirm_delete_"):
            confirm_delete_file(call, data[15:])
        elif data.startswith("cancel_delete_"):
            bot.answer_callback_query(call.id, "❌ Cancelled")
            show_user_files_callback(call)
        elif data == "coin_balance":
            bot.answer_callback_query(call.id, f"💰 Balance: {get_user_coins(user_id):,} 🪙")
        elif data == "coin_leaderboard":
            leaderboard_command(call.message); bot.answer_callback_query(call.id)
        elif data == "buy_file_slot":
            handle_buy_file_slot(call)
        elif data == "buy_premium":
            handle_buy_premium(call)
        elif data == "coin_transactions":
            show_coin_transactions(call)
        elif data == "refer_earn":
            refer_command(call.message); bot.answer_callback_query(call.id)
        elif data == "my_referrals":
            show_my_referrals(call)
        elif data == "referral_leaderboard":
            leaderboard_command(call.message); bot.answer_callback_query(call.id)
        elif data == "earned_coins":
            stats = get_referral_stats(user_id)
            bot.answer_callback_query(call.id, f"💰 Earned: {stats['earned_coins']:,} 🪙")
        elif data == "back_to_files":
            show_user_files_callback(call)
        elif data == "back_to_main":
            start_command(call.message); bot.answer_callback_query(call.id)
        elif data == "admin_stopall":
            stop_all_bots(call)
        elif data == "admin_refresh":
            refresh_admin_panel(call)
        elif data == "admin_fullstats":
            show_full_stats(call)
        elif data == "admin_logs":
            show_admin_logs(call)
        elif data == "admin_coins":
            show_admin_coin_panel(call)
        elif data == "admin_createcode":
            bot.answer_callback_query(call.id, "Use /createcode command")
        elif data == "admin_listcodes":
            listcodes_command(call.message); bot.answer_callback_query(call.id)
        elif data == "admin_listadmins":
            _list_admins(call.message); bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:50]}")

def show_file_actions(call, file_name):
    user_id  = call.from_user.id
    running  = is_bot_running(user_id, file_name)
    file_type= "py"
    for n, t in user_files.get(user_id, []):
        if n == file_name:
            file_type = t; break
    type_icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📄"
    status    = "🟢 Running" if running else "🔴 Stopped"
    text = f"""
╔══════════════════════════════════════╗
║     📄 <b>𝐇𝐎𝐒𝐓 𝐁𝐎𝐓: FILE</b> 📄         ║
╠══════════════════════════════════════╣
║ {type_icon} <b>Name:</b> <code>{file_name[:25]}</code>
║ 📁 Type: {file_type.upper()}
║ 📊 Status: {status}
║ 💰 Earned: +{COIN_PER_FILE_UPLOAD} 🪙
╚══════════════════════════════════════╝
"""
    markup = get_file_actions_keyboard(file_name, running)
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def run_user_script(call, file_name):
    user_id = call.from_user.id
    folder  = get_user_folder(user_id)
    path    = os.path.join(folder, file_name)
    if not os.path.exists(path):
        bot.answer_callback_query(call.id, "❌ File not found!"); return
    if is_bot_running(user_id, file_name):
        bot.answer_callback_query(call.id, "⚠️ Already running!"); return
    bot.answer_callback_query(call.id, "🚀 Starting...")
    class FakeMsg:
        def __init__(self, call): self.chat = call.message.chat; self.from_user = call.from_user
    if file_name.endswith('.py'):
        threading.Thread(target=run_script, args=(path, user_id, folder, file_name, FakeMsg(call))).start()
    elif file_name.endswith('.js'):
        threading.Thread(target=run_js_script, args=(path, user_id, folder, file_name, FakeMsg(call))).start()
    else:
        bot.send_message(call.message.chat.id, "❌ Unsupported file type!")

def stop_user_script(call, file_name):
    user_id    = call.from_user.id
    script_key = f"{user_id}_{file_name}"
    if script_key not in bot_scripts:
        bot.answer_callback_query(call.id, "❌ Script not running!"); return
    bot.answer_callback_query(call.id, "🔴 Stopping...")
    script_info = bot_scripts.get(script_key)
    if script_info:
        safe_kill_process_tree(script_info)
        cleanup_script(script_key)
        time.sleep(1)
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🟢 Run Again", callback_data=f"run_{file_name}"),
        types.InlineKeyboardButton("🔙 Back", callback_data="back_to_files")
    )
    text = f"✅ <b>Stopped</b>\n📄 <code>{file_name[:25]}</code>"
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    log_action(user_id, "SCRIPT_STOP", f"Stopped {file_name}")

def delete_user_file(call, file_name):
    user_id = call.from_user.id
    if is_bot_running(user_id, file_name):
        bot.answer_callback_query(call.id, "⚠️ Stop the script first!"); return
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_{file_name}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_delete_{file_name}")
    )
    try:
        bot.edit_message_text(f"⚠️ Delete <code>{file_name[:25]}</code>? This cannot be undone!",
                              call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        pass
    bot.answer_callback_query(call.id)

def confirm_delete_file(call, file_name):
    user_id = call.from_user.id
    folder  = get_user_folder(user_id)
    path    = os.path.join(folder, file_name)
    try:
        if os.path.exists(path):
            os.remove(path)
        if user_id in user_files:
            user_files[user_id] = [(n, t) for n, t in user_files[user_id] if n != file_name]
        remove_user_file_db(user_id, file_name)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🗂️ My Files", callback_data="back_to_files"))
        text = f"✅ <b>Deleted!</b>\n📄 <code>{file_name[:25]}</code>"
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                                  parse_mode='HTML', reply_markup=markup)
        except:
            bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
        bot.answer_callback_query(call.id, "✅ Deleted!")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def download_user_file(call, file_name):
    user_id = call.from_user.id
    path    = os.path.join(get_user_folder(user_id), file_name)
    if not os.path.exists(path):
        bot.answer_callback_query(call.id, "❌ File not found!"); return
    bot.answer_callback_query(call.id, "📥 Sending...")
    try:
        with open(path, 'rb') as f:
            bot.send_document(call.message.chat.id, f, caption=f"📄 {file_name}")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error: {str(e)[:100]}")

def show_script_logs(call, file_name):
    user_id    = call.from_user.id
    script_key = f"{user_id}_{file_name}"
    log_path   = os.path.join(LOGS_DIR, f"{script_key}.log")
    if not os.path.exists(log_path):
        bot.answer_callback_query(call.id, "📋 No logs yet"); return
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()[-2000:] or "No output yet..."
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔄 Refresh", callback_data=f"logs_{file_name}"),
            types.InlineKeyboardButton("🔙 Back", callback_data=f"file_{file_name}")
        )
        text = f"📋 <b>Logs:</b> <code>{file_name[:25]}</code>\n\n<code>{logs[:1500]}</code>"
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                                  parse_mode='HTML', reply_markup=markup)
        except telebot.apihelper.ApiTelegramException:
            bot.answer_callback_query(call.id, "📋 Logs unchanged")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:30]}")

def restart_user_script(call, file_name):
    user_id    = call.from_user.id
    script_key = f"{user_id}_{file_name}"
    if script_key in bot_scripts:
        safe_kill_process_tree(bot_scripts.get(script_key))
        cleanup_script(script_key)
        time.sleep(1)
    run_user_script(call, file_name)

def show_user_files_callback(call):
    class FakeMsg:
        def __init__(self, call): self.chat = call.message.chat; self.from_user = call.from_user
    show_user_files(FakeMsg(call))
    bot.answer_callback_query(call.id)

def stop_all_bots(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!"); return
    bot.answer_callback_query(call.id, "🔴 Stopping all bots...")
    stopped = 0
    for key in list(bot_scripts.keys()):
        try:
            safe_kill_process_tree(bot_scripts[key])
            cleanup_script(key)
            stopped += 1
        except:
            pass
    bot.send_message(call.message.chat.id, f"✅ Stopped {stopped} bots!")

def refresh_admin_panel(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!"); return
    class FakeMsg:
        def __init__(self, call): self.chat = call.message.chat; self.from_user = call.from_user
    show_admin_panel(FakeMsg(call))
    bot.answer_callback_query(call.id, "🔄 Refreshed!")

def show_full_stats(call):
    stats_command(call.message)
    bot.answer_callback_query(call.id)

def show_admin_logs(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!"); return
    logs = list(col_bot_logs.find().sort("_id", -1).limit(20))
    if logs:
        text = "📋 <b>Recent Logs</b>\n\n"
        for log in logs:
            text += f"👤 {log['user_id']} | {log['action']}\n{str(log['details'])[:30]}\n🕐 {log['timestamp'][:16]}\n\n"
    else:
        text = "📋 No logs."
    bot.send_message(call.message.chat.id, text[:4000], parse_mode='HTML')
    bot.answer_callback_query(call.id)

def handle_buy_file_slot(call):
    user_id = call.from_user.id
    coins   = get_user_coins(user_id)
    if coins < COIN_REDEEM_FILE_COST:
        bot.answer_callback_query(call.id, f"❌ Need {COIN_REDEEM_FILE_COST} 🪙, you have {coins:,}"); return
    success, msg = deduct_coins(user_id, COIN_REDEEM_FILE_COST, "file_slot_purchase", "Purchased extra file slot")
    if success:
        new_limit = get_user_file_limit(user_id)
        text = f"✅ <b>File Slot Purchased!</b>\n💰 Cost: {COIN_REDEEM_FILE_COST} 🪙\n📁 New limit: {new_limit}\n💵 Balance: {get_user_coins(user_id):,} 🪙"
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML')
        except:
            bot.send_message(call.message.chat.id, text, parse_mode='HTML')
        bot.answer_callback_query(call.id, "✅ Slot purchased!")
    else:
        bot.answer_callback_query(call.id, f"❌ {msg}")

def handle_buy_premium(call):
    user_id = call.from_user.id
    coins   = get_user_coins(user_id)
    if coins < COIN_REDEEM_PREMIUM_COST:
        bot.answer_callback_query(call.id, f"❌ Need {COIN_REDEEM_PREMIUM_COST} 🪙, you have {coins:,}"); return
    success, msg = deduct_coins(user_id, COIN_REDEEM_PREMIUM_COST, "premium_purchase", "Purchased 7-day premium")
    if success:
        expiry = datetime.now() + timedelta(days=7)
        user_subscriptions[user_id] = {'expiry': expiry}
        save_subscription(user_id, expiry)
        text = f"⭐ <b>Premium Activated!</b>\n💰 Cost: {COIN_REDEEM_PREMIUM_COST} 🪙\n📅 Expires: {expiry.strftime('%Y-%m-%d')}\n📁 Files: {SUBSCRIBED_USER_LIMIT}\n💵 Balance: {get_user_coins(user_id):,} 🪙"
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML')
        except:
            bot.send_message(call.message.chat.id, text, parse_mode='HTML')
        bot.answer_callback_query(call.id, "⭐ Premium activated!")
    else:
        bot.answer_callback_query(call.id, f"❌ {msg}")

def show_coin_transactions(call):
    user_id = call.from_user.id
    txns    = list(col_transactions.find({"user_id": user_id}).sort("_id", -1).limit(15))
    if not txns:
        text = "📜 <b>Transactions</b>\n\nNo transactions yet!"
    else:
        text = "📜 <b>Recent Transactions</b>\n\n"
        total_earned = total_spent = 0
        for t in txns:
            amt  = t["amount"]
            sign = "+" if amt > 0 else ""
            date = t["timestamp"][:16]
            if amt > 0: total_earned += amt
            else: total_spent += abs(amt)
            text += f"{sign}{amt} 🪙 | {t['transaction_type'][:15]}\n{t.get('description','')[:30]}\n{date}\n\n"
        text += f"📊 Earned: {total_earned:,} | Spent: {total_spent:,}\n💰 Balance: {get_user_coins(user_id):,} 🪙"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def show_my_referrals(call):
    user_id = call.from_user.id
    stats   = get_referral_stats(user_id)
    code    = generate_referral_code(user_id)
    me      = bot.get_me()
    text = f"👥 <b>My Referrals</b>\n\nTotal: {stats['total']}\nEarned: {stats['earned_coins']:,} 🪙\n\n"
    for i, ref_id in enumerate(stats['list'][:10], 1):
        text += f"{i}. 👤 {ref_id}\n"
    text += f"\n🔗 https://t.me/{me.username}?start={code}"
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📤 Share", switch_inline_query="Join Ghost Coder!"),
        types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
    )
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

def show_admin_coin_panel(call):
    user_id = call.from_user.id
    if user_id != OWNER_ID and user_id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Admin only!"); return
    total_coins = sum(user_coins.values())
    total_users = len(user_coins)
    text = f"👑 <b>Coin Admin</b>\n\n💰 Total Coins: {total_coins:,}\n👥 Users: {total_users}\n📊 Avg: {total_coins//max(total_users,1):,}\n🔑 Active Codes: {len(redeem_codes)}\n\n/createcode /givecoins /listcodes"
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔑 Create Code", callback_data="admin_createcode"),
        types.InlineKeyboardButton("📋 List Codes", callback_data="admin_listcodes")
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_main"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode='HTML', reply_markup=markup)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.answer_callback_query(call.id)

# ─────────────────────────────────────────────
#  SCRIPT RUNNERS
# ─────────────────────────────────────────────
def _start_process(cmd, script_path, user_id, user_folder, file_name, message):
    script_key = f"{user_id}_{file_name}"
    log_path   = os.path.join(LOGS_DIR, f"{script_key}.log")
    log_file   = open(log_path, 'w', encoding='utf-8')
    env        = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    process    = subprocess.Popen(cmd, cwd=user_folder, stdout=log_file,
                                  stderr=subprocess.STDOUT, env=env, text=True)
    bot_scripts[script_key] = {
        'process': process, 'log_file': log_file,
        'user_id': user_id, 'file_name': file_name,
        'start_time': datetime.now(), 'script_key': script_key
    }
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔴 Stop", callback_data=f"stop_{file_name}"),
        types.InlineKeyboardButton("📋 Logs", callback_data=f"logs_{file_name}")
    )
    markup.add(
        types.InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{file_name}"),
        types.InlineKeyboardButton("🔙 Back", callback_data="back_to_files")
    )
    bot.send_message(message.chat.id,
                     f"🟢 <b>Started!</b>\n📄 <code>{file_name}</code>\n🆔 PID: {process.pid}\n⏱️ {datetime.now().strftime('%H:%M:%S')}",
                     parse_mode='HTML', reply_markup=markup)
    log_action(user_id, "SCRIPT_RUN", f"Started {file_name} PID:{process.pid}")

def run_script(script_path, user_id, user_folder, file_name, message):
    try:
        _start_process(['python3', script_path], script_path, user_id, user_folder, file_name, message)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed to start {file_name}\nError: {str(e)[:100]}")
        log_action(user_id, "SCRIPT_ERROR", f"{file_name}: {str(e)}")

def run_js_script(script_path, user_id, user_folder, file_name, message):
    try:
        _start_process(['node', script_path], script_path, user_id, user_folder, file_name, message)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed to start {file_name}\nError: {str(e)[:100]}")
        log_action(user_id, "SCRIPT_ERROR", f"{file_name}: {str(e)}")

# ─────────────────────────────────────────────
#  CLEANUP & MAIN
# ─────────────────────────────────────────────
def cleanup_on_exit():
    logger.info("Cleaning up...")
    for key in list(bot_scripts.keys()):
        try:
            safe_kill_process_tree(bot_scripts[key])
        except:
            pass
atexit.register(cleanup_on_exit)

def main():
    if not TOKEN:
        logger.error("❌ TOKEN not set in environment variables!")
        sys.exit(1)
    if OWNER_ID == 0:
        logger.error("❌ OWNER_ID not set in environment variables!")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("🤖 Starting GHOST CODER Bot...")
    logger.info(f"👤 Owner: {OWNER_ID}")
    logger.info(f"🏷️ Brand: {YOUR_USERNAME}")
    logger.info(f"🆓 Free Limit: {FREE_USER_LIMIT}")
    logger.info("💰 MongoDB: Connected")
    logger.info("=" * 50)

    ensure_indexes()
    load_data()
    keep_alive()

    while True:
        try:
            logger.info("🚀 Bot polling started...")
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except requests.exceptions.ConnectionError:
            logger.error("Connection error! Retrying in 10s...")
            time.sleep(10)
        except requests.exceptions.ReadTimeout:
            logger.error("Read timeout! Retrying in 5s...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Bot error: {e}", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
