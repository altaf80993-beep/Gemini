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
GROUP_ID = os.getenv("GROUP_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Convert to int
PORT = int(os.getenv("PORT", 10000))

# =========================
# ESCROW ROOMS (Pre-created)
# =========================
# NOTE: Bot must be admin in all these groups!
# Add your actual pre-created room IDs and invite links

ROOMS = [
    {
        "room_num": 1,
        "room_id": -1001111111111,  # Replace with actual group ID
        "invite_link": "https://t.me/+FbR1VOqqXqswYjJl",  # Replace with actual link
        "busy": False
    },
    {
        "room_num": 2,
        "room_id": -1002222222222,  # Replace with actual group ID
        "invite_link": "https://t.me/+_5lw9u-sBM0xY2Jl",  # Replace with actual link
        "busy": False
    },
    # Add more rooms as needed
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
# DATABASE
# =========================

DB_NAME = "database.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
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

def has_active_escrow(username):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        SELECT * FROM active_escrows
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
    
    # Get escrow details first
    c.execute("SELECT * FROM active_escrows WHERE room_id=?", (room_id,))
    escrow = c.fetchone()
    
    if escrow:
        # Save to history
        c.execute("""
            INSERT INTO escrow_history 
            (seller_username, buyer_username, room_num, start_time, end_time, status, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (escrow[0], escrow[1], escrow[3], escrow[4], end_time, status, duration))
        
        # Remove from active
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

# =========================
# ALLOWED SHORT MESSAGES
# =========================

ALLOWED_MESSAGES = [
    "dm", "hi", "hello", "check dm", "done", 
    "paid", "sent", "ok", "yes", "available"
]

# =========================
# CHECK GROUP MEMBERSHIP
# =========================

async def is_member_of_group(context: ContextTypes.DEFAULT_TYPE, username: str) -> bool:
    try:
        member = await context.bot.get_chat_member(GROUP_ID, username)
        return member.status not in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]
    except Exception:
        return False

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
        await msg.reply_text("❌ Please set a Telegram username first!")
        return
    
    # Check active escrow
    if has_active_escrow(seller_username):
        await msg.reply_text(f"❌ @{seller_username} already has an active escrow deal!")
        return
    
    if has_active_escrow(buyer_username):
        await msg.reply_text(f"❌ @{buyer_username} already has an active escrow deal!")
        return
    
    # Check group membership
    if not await is_member_of_group(context, buyer_username):
        await msg.reply_text(
            f"⚠️ @{buyer_username} is not a member of this group!\n"
            f"Please be cautious - they may be banned."
        )
        return
    
    # Get free room
    room = get_free_room()
    
    if not room:
        await msg.reply_text("❌ All escrow rooms are busy! Please try again later.")
        return
    
    # Store in database
    start_time = time.time()
    add_active_escrow(
        seller_username, 
        buyer_username, 
        room["room_id"], 
        room["room_num"], 
        start_time,
        seller.id,
        None  # Buyer ID unknown yet, will be updated when they join
    )
    
    # Send invite to seller
    invite_text = (
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Buyer: @{buyer_username}\n"
        f"🔗 Join Link: {room['invite_link']}\n\n"
        f"📝 Use /complete after payment\n"
        f"❌ Use /cancel to cancel deal"
    )
    
    try:
        await context.bot.send_message(chat_id=seller.id, text=invite_text)
    except Exception as e:
        logger.error(f"Failed to DM seller: {e}")
    
    # Send invite to buyer
    try:
        await context.bot.send_message(
            chat_id=buyer_username, 
            text=f"✅ Escrow Room #{room['room_num']} Created!\n\n👤 Seller: @{seller_username}\n🔗 Join Link: {room['invite_link']}"
        )
    except Exception as e:
        logger.error(f"Failed to DM buyer: {e}")
    
    # Send confirmation in main group
    await msg.reply_text(
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"🔗 Invite link sent via DM to both parties."
    )
    
    # Also send to main group channel
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"🏦 New Escrow Started - Room #{room['room_num']}\n"
            f"Seller: @{seller_username}\n"
            f"Buyer: @{buyer_username}\n"
            f"Time: {time.strftime('%I:%M %p')}"
        ),
        disable_notification=False
    )

# =========================
# /COMPLETE COMMAND
# =========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg:
        return
    
    room_id = msg.chat_id
    escrow = get_active_escrow_by_room(room_id)
    
    if not escrow:
        await msg.reply_text("❌ No active escrow found in this room.")
        return
    
    end_time = time.time()
    duration_sec = int(end_time - escrow["start_time"])
    minutes = duration_sec // 60
    seconds = duration_sec % 60
    
    # Remove from active and save to history
    remove_escrow(room_id, "completed", end_time, duration_sec)
    
    # Free the room for reuse
    free_room(room_id)
    
    await msg.reply_text(
        f"✅ Deal Completed!\n\n"
        f"Duration: {minutes}m {seconds}s\n"
        f"Room #{escrow['room_num']} is now free for new deals."
    )
    
    # Broadcast to main group
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"✅ Status: Deal Completed\n"
            f"between @{escrow['seller_username']} & @{escrow['buyer_username']}\n"
            f"Escrow Room #{escrow['room_num']}\n"
            f"Completed in {minutes}m {seconds}s"
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
    escrow = get_active_escrow_by_room(room_id)
    
    if not escrow:
        await msg.reply_text("❌ No active escrow found in this room.")
        return
    
    end_time = time.time()
    duration_sec = int(end_time - escrow["start_time"])
    minutes = duration_sec // 60
    seconds = duration_sec % 60
    
    remove_escrow(room_id, "cancelled", end_time, duration_sec)
    free_room(room_id)
    
    await msg.reply_text(
        f"❌ Deal Cancelled.\n\n"
        f"Duration: {minutes}m {seconds}s\n"
        f"Room #{escrow['room_num']} is now free."
    )
    
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"❌ Status: Deal Cancelled\n"
            f"between @{escrow['seller_username']} & @{escrow['buyer_username']}\n"
            f"Escrow Room #{escrow['room_num']}\n"
            f"Cancelled after {minutes}m {seconds}s"
        )
    )

# =========================
# TELEGRAM FILTER
# =========================

async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg or not msg.text:
        return
    
    chat = msg.chat
    
    # Fix: Proper lower() call
    chat_username = (chat.username or "").lower()
    target_group = GROUP_ID.lstrip("@").lower()
    
    # Only work in target group
    if chat_username != target_group and str(chat.id) != GROUP_ID:
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
                    "❌ Invalid post format\n\n"
                    "Please use this format:\n\n"
                    "#buying or #selling\n\n"
                    "Chain: BEP20\n"
                    "Amount[USDT]: ?\n"
                    "Amount[INR]: ?\n"
                    "Rate[INR/USDT]: ?\n"
                    "Payment method: ?"
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
    return "Escrow Bot Running!"

# =========================
# START BOT
# =========================

async def start_bot():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
    # Add message filter
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_msg))
    
    logger.info("Bot started...")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
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
    logger.info("Starting services...")
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    flask_app.run(host="0.0.0.0", port=PORT)
