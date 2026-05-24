import re
import os
import time
import sqlite3
import logging
import threading
import asyncio

from flask import Flask
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatMemberStatus

# =========================
# LOAD ENV
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID_INPUT = os.getenv("GROUP_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", 10000))

# Global variable for resolved group ID
GROUP_ID = GROUP_ID_INPUT

# =========================
# PRE-CREATED ESCROW ROOMS
# =========================
# ⚠️ IMPORTANT: Bot must be ADMIN in all these rooms!
# Run /getid command in each room to get the actual group ID

ROOMS = [
    {
        "room_num": 1,
        "room_id": -1003974490347,  # 🔁 Replace with Room 1 actual ID (run /getid)
        "invite_link": "https://t.me/+_5lw9u-sBM0xY2Jl",  # Your room 1 invite link
        "busy": False
    },
    {
        "room_num": 2,
        "room_id": -1003766531525,  # 🔁 Replace with Room 2 actual ID (run /getid)
        "invite_link": "https://t.me/+FbR1VOqqXqswYjJl",  # Your room 2 invite link
        "busy": False
    },
]

# =========================
# LOGGING
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# =========================
# DATABASE SETUP
# =========================

DB_NAME = "escrow.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Table for group members (store user IDs by username)
    c.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            last_seen REAL
        )
    """)
    
    # Table for active escrows
    c.execute("""
        CREATE TABLE IF NOT EXISTS active_escrows (
            seller_username TEXT,
            buyer_username TEXT,
            room_id INTEGER,
            room_num INTEGER,
            start_time REAL,
            seller_id INTEGER,
            buyer_id INTEGER
        )
    """)
    
    # Table for escrow history
    c.execute("""
        CREATE TABLE IF NOT EXISTS escrow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_username TEXT,
            buyer_username TEXT,
            room_num INTEGER,
            start_time REAL,
            end_time REAL,
            status TEXT,
            duration_seconds INTEGER
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def save_member_info(user_id, username, first_name, last_name=None):
    """Save or update member info in database"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        INSERT OR REPLACE INTO group_members 
        (user_id, username, first_name, last_name, last_seen)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, first_name, last_name, time.time()))
    
    conn.commit()
    conn.close()

def get_user_by_username(username):
    """Get user ID from database by username"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        SELECT user_id, username, first_name FROM group_members 
        WHERE LOWER(username) = LOWER(?) 
        ORDER BY last_seen DESC LIMIT 1
    """, (username,))
    
    result = c.fetchone()
    conn.close()
    
    if result:
        return {"user_id": result[0], "username": result[1], "first_name": result[2]}
    return None

def has_active_escrow(username):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        SELECT 1 FROM active_escrows
        WHERE seller_username=? OR buyer_username=?
    """, (username, username))
    
    result = c.fetchone()
    conn.close()
    return result is not None

def add_active_escrow(seller_username, buyer_username, room_id, room_num, start_time, seller_id, buyer_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        INSERT INTO active_escrows
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (seller_username, buyer_username, room_id, room_num, start_time, seller_id, buyer_id))
    
    conn.commit()
    conn.close()

def get_active_escrow_by_room(room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT * FROM active_escrows WHERE room_id=?", (room_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return None
    
    return {
        "seller_username": result[0],
        "buyer_username": result[1],
        "room_id": result[2],
        "room_num": result[3],
        "start_time": result[4],
        "seller_id": result[5],
        "buyer_id": result[6]
    }

def remove_escrow(room_id, status, end_time, duration):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT * FROM active_escrows WHERE room_id=?", (room_id,))
    escrow = c.fetchone()
    
    if escrow:
        c.execute("""
            INSERT INTO escrow_history 
            (seller_username, buyer_username, room_num, start_time, end_time, status, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (escrow[0], escrow[1], escrow[3], escrow[4], end_time, status, duration))
        
        c.execute("DELETE FROM active_escrows WHERE room_id=?", (room_id,))
    
    conn.commit()
    conn.close()

def get_free_room():
    for room in ROOMS:
        if not room["busy"]:
            room["busy"] = True
            return room
    return None

def free_room(room_id):
    for room in ROOMS:
        if room["room_id"] == room_id:
            room["busy"] = False
            break

# =========================
# MESSAGE FORMAT CHECK
# =========================

def is_valid(text: str) -> bool:
    pattern = re.compile(
        r"^(#buying|#selling)\s*[\r\n]+"
        r"Chain:\s*.+[\r\n]+"
        r"Amount\[USDT\]:\s*.+[\r\n]+"
        r"Amount\[INR\]:\s*.+[\r\n]+"
        r"Rate\[INR/USDT\]:\s*.+[\r\n]+"
        r"Payment method:\s*.+",
        re.IGNORECASE
    )
    return bool(pattern.match(text.strip()))

ALLOWED_MESSAGES = [
    "dm", "hi", "hello", "check dm", "done", 
    "paid", "sent", "ok", "yes", "available"
]

# =========================
# TRACK MEMBERS
# =========================

async def track_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-save member info when they send message in group"""
    msg = update.message
    if not msg:
        return
    
    chat = msg.chat
    
    # Only track in target group
    chat_username = (chat.username or "").lower()
    target_group = str(GROUP_ID_INPUT).lstrip("@").lower()
    
    if chat_username != target_group and str(chat.id) != str(GROUP_ID):
        return
    
    user = msg.from_user
    save_member_info(user.id, user.username, user.first_name, user.last_name)

# =========================
# /GETID COMMAND (Get Group ID)
# =========================

async def get_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get current group ID - /getid"""
    msg = update.message
    
    if not msg:
        return
    
    chat = msg.chat
    
    if chat.type == "supergroup":
        await msg.reply_text(
            f"📊 **Group Info**\n\n"
            f"📝 Name: {chat.title}\n"
            f"🆔 Group ID: `{chat.id}`\n"
            f"🔗 Username: @{chat.username if chat.username else 'None'}\n\n"
            f"Copy this ID for ROOMS list: `{chat.id}`"
        )
    else:
        await msg.reply_text("❌ This is not a supergroup!")

# =========================
# /ESCROW COMMAND
# =========================

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg:
        return
    
    if len(context.args) < 1:
        await msg.reply_text(
            "❌ Usage: /escrow @buyer_username\n\n"
            "Example: /escrow @john_doe"
        )
        return
    
    seller = msg.from_user
    buyer_username = context.args[0].lstrip("@").lower()
    seller_username = seller.username.lower() if seller.username else None
    
    if not seller_username:
        await msg.reply_text("❌ Please set a Telegram username first!\n\nSettings → Edit Username")
        return
    
    # Save seller info
    save_member_info(seller.id, seller.username, seller.first_name)
    
    # Check active escrow
    if has_active_escrow(seller_username):
        await msg.reply_text(f"❌ @{seller_username} already has an active escrow deal!")
        return
    
    if has_active_escrow(buyer_username):
        await msg.reply_text(f"❌ @{buyer_username} already has an active escrow deal!")
        return
    
    # Get free room
    room = get_free_room()
    
    # Store in database
    start_time = time.time()
    add_active_escrow(
        seller_username, 
        buyer_username, 
        room["room_id"], 
        room["room_num"], 
        start_time,
        seller.id,
        buyer_id
    )
    
    # Send invite to seller
    seller_text = (
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Buyer: @{buyer_username}\n"
        f"🔗 Join Link: {room['invite_link']}\n\n"
        f"📝 Commands:\n"
        f"• /complete - After payment received\n"
        f"• /cancel - To cancel deal\n\n"
        f"⚠️ Only you, buyer, and admin can join!"
    )
    
    try:
        await context.bot.send_message(chat_id=seller.id, text=seller_text)
        logger.info(f"DM sent to seller @{seller_username}")
    except Exception as e:
        logger.error(f"Failed to DM seller: {e}")
    
    # Send invite to buyer
    buyer_text = (
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"🔗 Join Link: {room['invite_link']}\n\n"
        f"📝 After payment, seller will use /complete\n"
        f"❌ Use /cancel to cancel deal\n\n"
        f"⚠️ Only you, seller, and admin can join!"
    )
    
    try:
        await context.bot.send_message(chat_id=buyer_id, text=buyer_text)
        logger.info(f"DM sent to buyer @{buyer_username}")
    except Exception as e:
        logger.error(f"Failed to DM buyer: {e}")
    
    # Send confirmation in main group
    await msg.reply_text(
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"📩 Invite link sent via DM to both parties!\n"
        f"⚠️ Check your Private Messages."
    )
    
    # Broadcast to main group
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"🏦 New Escrow Started - Room #{room['room_num']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Seller: @{seller_username}\n"
            f"👤 Buyer: @{buyer_username}\n"
            f"🕐 Time: {time.strftime('%I:%M %p')}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
    )
    
    logger.info(f"✅ Escrow #{room['room_num']}: {seller_username} <-> {buyer_username}")

# =========================
# /COMPLETE COMMAND
# =========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg:
        return
    
    room_id = msg.chat_id
    user = msg.from_user
    escrow = get_active_escrow_by_room(room_id)
    
    if not escrow:
        await msg.reply_text("❌ No active escrow found in this room.")
        return
    
    # Check if user is seller, buyer, or admin
    if user.id != escrow['seller_id'] and user.id != escrow['buyer_id'] and user.id != ADMIN_ID:
        await msg.reply_text("❌ Only seller, buyer, or admin can complete this deal!")
        return
    
    end_time = time.time()
    duration_sec = int(end_time - escrow["start_time"])
    minutes = duration_sec // 60
    seconds = duration_sec % 60
    
    remove_escrow(room_id, "completed", end_time, duration_sec)
    free_room(room_id)
    
    await msg.reply_text(
        f"✅ DEAL COMPLETED!\n\n"
        f"⏱️ Duration: {minutes}m {seconds}s\n"
        f"🏠 Room #{escrow['room_num']} is now free.\n\n"
        f"Thank you for using Escrow Bot!"
    )
    
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"✅ Deal Completed!\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Seller: @{escrow['seller_username']}\n"
            f"👤 Buyer: @{escrow['buyer_username']}\n"
            f"🏠 Room #{escrow['room_num']}\n"
            f"⏱️ Time: {minutes}m {seconds}s\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
    )

# =========================
# /CANCEL COMMAND
# =========================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg:
        return
    
    room_id = msg.chat_id
    user = msg.from_user
    escrow = get_active_escrow_by_room(room_id)
    
    if not escrow:
        await msg.reply_text("❌ No active escrow found in this room.")
        return
    
    if user.id != escrow['seller_id'] and user.id != escrow['buyer_id'] and user.id != ADMIN_ID:
        await msg.reply_text("❌ Only seller, buyer, or admin can cancel this deal!")
        return
    
    end_time = time.time()
    duration_sec = int(end_time - escrow["start_time"])
    minutes = duration_sec // 60
    seconds = duration_sec % 60
    
    remove_escrow(room_id, "cancelled", end_time, duration_sec)
    free_room(room_id)
    
    await msg.reply_text(
        f"❌ DEAL CANCELLED!\n\n"
        f"⏱️ Duration: {minutes}m {seconds}s\n"
        f"🏠 Room #{escrow['room_num']} is now free."
    )
    
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"❌ Deal Cancelled!\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Seller: @{escrow['seller_username']}\n"
            f"👤 Buyer: @{escrow['buyer_username']}\n"
            f"🏠 Room #{escrow['room_num']}\n"
            f"⏱️ Cancelled after: {minutes}m {seconds}s\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
    )

# =========================
# MESSAGE FILTER
# =========================

async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg or not msg.text:
        return
    
    chat = msg.chat
    
    chat_username = (chat.username or "").lower()
    target_group = str(GROUP_ID_INPUT).lstrip("@").lower()
    
    if chat_username != target_group and str(chat.id) != str(GROUP_ID):
        return
    
    # Allow admins
    try:
        member = await context.bot.get_chat_member(msg.chat_id, msg.from_user.id)
        if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return
    except Exception as e:
        logger.error(f"Member check error: {e}")
    
    # Allow short replies
    if msg.text.strip().lower() in ALLOWED_MESSAGES:
        return
    
    # Delete invalid format messages
    if not is_valid(msg.text):
        try:
            await msg.delete()
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=(
                    "❌ Invalid Post Format!\n\n"
                    "Please use this format:\n\n"
                    "#buying or #selling\n\n"
                    "Chain: BEP20\n"
                    "Amount[USDT]: ?\n"
                    "Amount[INR]: ?\n"
                    "Rate[INR/USDT]: ?\n"
                    "Payment method: ?\n\n"
                    "⚠️ Only BEP20, POL chains allowed!"
                ),
                reply_to_message_id=msg.message_id
            )
        except Exception as e:
            logger.error(f"Delete error: {e}")

# =========================
# FLASK APP
# =========================

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "🤖 Escrow Bot is Running!"

@flask_app.route("/health")
def health():
    return "OK", 200

# =========================
# RESOLVE GROUP ID
# =========================

async def resolve_group_id(app: Application):
    """Convert username to numeric group ID if needed"""
    global GROUP_ID
    
    try:
        # If already numeric
        if str(GROUP_ID_INPUT).lstrip("-").isdigit():
            GROUP_ID = int(GROUP_ID_INPUT)
            logger.info(f"✅ Using numeric group ID: {GROUP_ID}")
            return
        
        # Resolve username
        chat = await app.bot.get_chat(GROUP_ID_INPUT)
        GROUP_ID = chat.id
        logger.info(f"✅ Resolved @{GROUP_ID_INPUT} to ID: {GROUP_ID}")
    except Exception as e:
        logger.error(f"❌ Failed to resolve group: {e}")
        GROUP_ID = GROUP_ID_INPUT

# =========================
# START BOT
# =========================

async def start_bot():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("getid", get_id_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
    # Add message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_msg))
    app.add_handler(MessageHandler(filters.ALL, track_members))
    
    logger.info("🚀 Escrow Bot Starting...")
    
    await app.initialize()
    await app.start()
    
    # Resolve group ID
    await resolve_group_id(app)
    
    await app.updater.start_polling()
    logger.info("✅ Bot is running!")
    
    while True:
        await asyncio.sleep(100)

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_bot())

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    logger.info("Starting Escrow Bot Services...")
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    flask_app.run(host="0.0.0.0", port=PORT)
