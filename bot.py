import re
import os
import time
import sqlite3
import logging
import threading
import asyncio
from flask import Flask
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler, ContextTypes,
    filters, ChatMemberHandler, CallbackQueryHandler
)
from telegram.constants import ChatMemberStatus

# =========================
# LOAD ENV & CONFIG
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID_INPUT = os.getenv("GROUP_ID")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@Crypto_8099")
ADMIN_WALLET = os.getenv("ADMIN_WALLET", "0xYourAdminWalletAddressHere")
PORT = int(os.getenv("PORT", "10000"))

# Escrow Rooms Configuration
ROOMS = [
    {"room_num": 1, "room_id": -1003970953090, "invite_link": "https://t.me/+BHJL7ayHNJw3OTE1", "busy": False},
    {"room_num": 2, "room_id": -1003766531525, "invite_link": "https://t.me/+FbR1VOqqXqswYjJl", "busy": False},
]

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# DATABASE LOGIC
# =========================
DB_NAME = "escrow.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Users table - no verification needed
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        join_time REAL
    )""")
    # Active escrows table
    c.execute("""CREATE TABLE IF NOT EXISTS active_escrows (
        room_id INTEGER PRIMARY KEY,
        room_num INTEGER,
        user1_username TEXT,
        user2_username TEXT,
        user1_id INTEGER DEFAULT 0,
        user2_id INTEGER DEFAULT 0,
        seller_id INTEGER DEFAULT 0,
        seller_username TEXT DEFAULT '',
        buyer_id INTEGER DEFAULT 0,
        buyer_username TEXT DEFAULT '',
        start_time REAL
    )""")
    # Completed deals table
    c.execute("""CREATE TABLE IF NOT EXISTS completed_deals (
        deal_id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_num INTEGER,
        seller_username TEXT,
        buyer_username TEXT,
        start_time REAL,
        end_time REAL,
        status TEXT,
        reason TEXT,
        cancelled_by TEXT
    )""")
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def get_main_group_id():
    try:
        if str(GROUP_ID_INPUT).lstrip('-').isdigit():
            return int(GROUP_ID_INPUT)
    except:
        pass
    return GROUP_ID_INPUT

def get_active_escrow(room_id):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM active_escrows WHERE room_id=?", (room_id,))
    result = c.fetchone()
    conn.close()
    return dict(result) if result else None

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

def save_user(user):
    """Save user to database"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, join_time) VALUES (?, ?, ?, ?)",
              (user.id, user.username if user.username else user.first_name, user.first_name, time.time()))
    conn.commit()
    conn.close()

def is_admin(user):
    """Check if user is admin by username"""
    if not user or not user.username:
        return False
    admin_clean = ADMIN_USERNAME.replace('@', '').lower()
    user_clean = user.username.lower()
    return admin_clean == user_clean

# =========================
# VALIDATION UTILS
# =========================
ALLOWED_WORDS = [
    "hi", "hello", "hey", "dm", "check dm", "done", "paid", "sent",
    "received", "ok", "yes", "no", "available", "confirm", "pending",
    "wait", "waiting", "thanks", "thank you", "welcome", "got it", "okay", "fine",
    "kon", "kaise", "ho", "kya", "haal", "main", "hu", "mera", "tera", "kese"
]

def is_allowed_word(text: str) -> bool:
    return text.lower().strip() in ALLOWED_WORDS

def is_valid_trade_post(text: str) -> bool:
    pattern = re.compile(
        r"^(#buying|#selling)\s*\n+"
        r"Chain:\s*.+\n+"
        r"Amount\[USDT\]:\s*.+\n+"
        r"Amount\[INR\]:\s*.+\n+"
        r"Rate\[INR/USDT\]:\s*.+\n+"
        r"Payment method:\s*.+",
        re.IGNORECASE
    )
    return bool(pattern.match(text.strip()))

# =========================
# MAIN GROUP HANDLERS
# =========================

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save user on join, send welcome message in group only"""
    main_group_id = get_main_group_id()
    
    # Handle different types of join events
    user = None
    if update.chat_member:
        if update.chat_member.chat.id != main_group_id:
            return
        if update.chat_member.new_chat_member.status != ChatMemberStatus.MEMBER:
            return
        user = update.chat_member.new_chat_member.user
    elif update.message and update.message.new_chat_members:
        if update.message.chat.id != main_group_id:
            return
        user = update.message.new_chat_members[0]
    else:
        return

    if not user:
        return
    
    username = f"@{user.username}" if user.username else user.first_name
    
    # Save user to database
    save_user(user)
    
    # Welcome message in group only
    welcome_text = f"welcome {username} to escrow spartans group"
    await context.bot.send_message(chat_id=main_group_id, text=welcome_text)

async def filter_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main group filter - only trade posts and allowed words"""
    msg = update.message
    if not msg or not msg.text:
        return
    
    main_group_id = get_main_group_id()
    if msg.chat.id != main_group_id:
        return
    
    # Admin messages allowed
    if is_admin(msg.from_user):
        return
    
    if msg.text.startswith('/'):
        return
    if is_allowed_word(msg.text):
        return
    
    # Check for trade post
    if msg.text.lower().startswith(('#buying', '#selling')):
        if is_valid_trade_post(msg.text):
            return
        else:
            await msg.delete()
            await msg.reply_text(
                "❌ **Invalid Trade Post Format!**\n\n"
                "📝 **Use this exact format:**\n"
                "```\n"
                "#buying or #selling\n\n"
                "Chain: BEP20\n"
                "Amount[USDT]: 100\n"
                "Amount[INR]: 8500\n"
                "Rate[INR/USDT]: 85\n"
                "Payment method: Bank/UPI\n"
                "```",
                parse_mode='Markdown'
            )
            return
    
    # Delete all other messages
    await msg.delete()
    await msg.reply_text(
        "❌ **Message Deleted!**\n\n"
        "📝 **Only allowed:**\n"
        "• Trade posts (#buying/#selling format)\n"
        "• Commands: /escrow\n"
        "• Simple words: hi, hello, ok, yes, no, etc.",
        parse_mode='Markdown'
    )

# =========================
# ESCROW COMMAND
# =========================

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create escrow room"""
    msg = update.message
    if not msg or len(context.args) < 1:
        await msg.reply_text("❌ Usage: /escrow @username")
        return

    u1_user = msg.from_user
    u1_username = u1_user.username if u1_user.username else None
    u2_username_raw = context.args[0].replace("@", "")

    if not u1_username:
        await msg.reply_text("❌ Please set a Telegram username first!")
        return

    # Save user if not exists
    save_user(u1_user)
    
    # Check if second user exists in database (has joined group at least once)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, username FROM users WHERE LOWER(username)=?", (u2_username_raw.lower(),))
    u2_data = c.fetchone()
    conn.close()

    if not u2_data:
        await msg.reply_text(f"❌ User @{u2_username_raw} not found! Ask them to join the group first.")
        return
    
    u2_id = u2_data[0]
    u2_username = u2_data[1]

    room = get_free_room()
    if not room:
        await msg.reply_text("❌ All rooms busy! Please wait.")
        return

    # Save to database
    current_time = time.time()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO active_escrows 
              (room_id, room_num, user1_username, user2_username, user1_id, user2_id, start_time) 
              VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (room["room_id"], room["room_num"], u1_username, u2_username, u1_user.id, u2_id, current_time))
    conn.commit()
    conn.close()

    # Role selection buttons
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👨‍💼 I AM SELLER", callback_data=f"role_seller_{room['room_id']}"),
         InlineKeyboardButton("👨‍💻 I AM BUYER", callback_data=f"role_buyer_{room['room_id']}")]
    ])

    await context.bot.send_message(
        chat_id=room["room_id"],
        text=f"🔒 **ESCROW ROOM #{room['room_num']}**\n\n👥 @{u1_username}  |  @{u2_username}\n\n👇 **Select your role:**",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    
    await msg.reply_text(f"✅ Escrow Room Created!\n🔗 Join: {room['invite_link']}")

async def role_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple role selection - just give address or wait message"""
    query = update.callback_query
    user = query.from_user
    data = query.data.split("_")
    role, room_id = data[1], int(data[2])

    escrow = get_active_escrow(room_id)
    if not escrow or user.id not in [escrow["user1_id"], escrow["user2_id"]]:
        await query.answer("❌ You are not part of this deal!", show_alert=True)
        return

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    if role == "seller":
        c.execute("UPDATE active_escrows SET seller_id=?, seller_username=? WHERE room_id=?", 
                  (user.id, user.username or user.first_name, room_id))
        conn.commit()
        conn.close()
        
        await query.message.reply_text(
            f"✅ @{user.username} you are **SELLER**\n\n"
            f"💰 Send USDT to Admin Wallet:\n"
            f"`{ADMIN_WALLET}`\n\n"
            f"📝 After sending, tell admin in this room.",
            parse_mode='Markdown'
        )
        
    elif role == "buyer":
        c.execute("UPDATE active_escrows SET buyer_id=?, buyer_username=? WHERE room_id=?", 
                  (user.id, user.username or user.first_name, room_id))
        conn.commit()
        conn.close()
        
        await query.message.reply_text(
            f"✅ @{user.username} you are **BUYER**\n\n"
            f"⚠️ **WAIT for admin confirmation**\n"
            f"❌ **DO NOT pay INR yet**\n\n"
            f"Admin will tell you when to pay.",
            parse_mode='Markdown'
        )
    
    await query.answer()

async def handle_security(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-kick unauthorized users from escrow rooms"""
    chat_member = update.chat_member
    if not chat_member or chat_member.new_chat_member.status != ChatMemberStatus.MEMBER:
        return

    room_id = chat_member.chat.id
    new_user = chat_member.new_chat_member.user
    
    # Don't kick admin or bot
    if is_admin(new_user):
        return
    if new_user.id == context.bot.id:
        return

    escrow = get_active_escrow(room_id)
    if escrow:
        allowed_ids = [escrow["user1_id"], escrow["user2_id"]]
        if new_user.id not in allowed_ids:
            await context.bot.ban_chat_member(room_id, new_user.id)
            await context.bot.unban_chat_member(room_id, new_user.id)
            logger.info(f"Kicked unauthorized user {new_user.id} from Room {room_id}")

# =========================
# CANCEL & COMPLETE HANDLERS
# =========================

# Cancel reasons
BUYER_CANCEL_REASONS = {
    "1": "Seller didn't send USDT",
    "2": "Seller left the room",
    "3": "Seller not responding"
}

SELLER_CANCEL_REASONS = {
    "1": "Buyer didn't pay INR",
    "2": "Buyer left the room",
    "3": "Buyer not responding"
}

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User types 'cancel' in escrow room"""
    msg = update.message
    if not msg or not msg.text or msg.text.lower().strip() != "cancel":
        return
    
    user = update.effective_user
    room_id = update.effective_chat.id
    escrow = get_active_escrow(room_id)
    
    if not escrow:
        await msg.reply_text("❌ No active deal in this room")
        return
    
    # Check if user is seller or buyer
    if user.id == escrow.get("seller_id"):
        # Seller cancel reasons
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Buyer didn't pay INR", callback_data="cancel_seller_1")],
            [InlineKeyboardButton("🚪 Buyer left the room", callback_data="cancel_seller_2")],
            [InlineKeyboardButton("⏳ Buyer not responding", callback_data="cancel_seller_3")]
        ])
        await msg.reply_text(
            "❌ **Select reason for cancellation:**",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    elif user.id == escrow.get("buyer_id"):
        # Buyer cancel reasons
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Seller didn't send USDT", callback_data="cancel_buyer_1")],
            [InlineKeyboardButton("🚪 Seller left the room", callback_data="cancel_buyer_2")],
            [InlineKeyboardButton("⏳ Seller not responding", callback_data="cancel_buyer_3")]
        ])
        await msg.reply_text(
            "❌ **Select reason for cancellation:**",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    else:
        await msg.reply_text("❌ You are not part of this deal!")

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel reason selection"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split("_")
    user_type = parts[1]  # seller or buyer
    reason_num = parts[2]  # 1, 2, or 3
    
    room_id = update.effective_chat.id
    escrow = get_active_escrow(room_id)
    
    if not escrow:
        await query.message.reply_text("❌ No active deal found")
        return
    
    # Get reason text
    if user_type == "seller":
        reason = SELLER_CANCEL_REASONS[reason_num]
        cancelled_by = "Seller"
    else:
        reason = BUYER_CANCEL_REASONS[reason_num]
        cancelled_by = "Buyer"
    
    # Calculate time wasted
    duration = int(time.time() - escrow['start_time'])
    minutes = duration // 60
    seconds = duration % 60
    
    # Send notification to main group
    main_group_id = get_main_group_id()
    cancel_text = f"""
❌ **DEAL CANCELLED** - ROOM #{escrow['room_num']}
━━━━━━━━━━━━━━━━━━━━━
👨‍💼 Seller: @{escrow['user1_username']}
👨‍💻 Buyer: @{escrow['user2_username']}
⏱️ Time wasted: {minutes}m {seconds}s
❌ Cancelled by: {cancelled_by}
❌ Reason: {reason}
━━━━━━━━━━━━━━━━━━━━━
"""
    await context.bot.send_message(chat_id=main_group_id, text=cancel_text, parse_mode='Markdown')
    
    # Save to completed deals history
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""INSERT INTO completed_deals 
              (room_num, seller_username, buyer_username, start_time, end_time, status, reason, cancelled_by) 
              VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (escrow['room_num'], escrow['user1_username'], escrow['user2_username'],
               escrow['start_time'], time.time(), "cancelled", reason, cancelled_by))
    conn.commit()
    conn.close()
    
    # Free room and delete from active escrows
    free_room(room_id)
    conn = sqlite3.connect(DB_NAME)
    conn.execute("DELETE FROM active_escrows WHERE room_id=?", (room_id,))
    conn.commit()
    conn.close()
    
    await query.message.reply_text(f"❌ Deal cancelled!\nReason: {reason}\nRoom is now free.")

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin types 'complete' in escrow room"""
    msg = update.message
    if not msg or not msg.text or msg.text.lower().strip() != "complete":
        return
    
    # Check if user is admin
    user = update.effective_user
    if not is_admin(user):
        await msg.reply_text("❌ Only admin can complete the deal!")
        return
    
    room_id = update.effective_chat.id
    escrow = get_active_escrow(room_id)
    
    if not escrow:
        await msg.reply_text("❌ No active deal in this room")
        return
    
    # Calculate time taken
    duration = int(time.time() - escrow['start_time'])
    minutes = duration // 60
    seconds = duration % 60
    
    # Send notification to main group
    main_group_id = get_main_group_id()
    complete_text = f"""
✅ **DEAL COMPLETED** - ROOM #{escrow['room_num']}
━━━━━━━━━━━━━━━━━━━━━
👨‍💼 Seller: @{escrow['user1_username']}
👨‍💻 Buyer: @{escrow['user2_username']}
⏱️ Time taken: {minutes}m {seconds}s
━━━━━━━━━━━━━━━━━━━━━
"""
    await context.bot.send_message(chat_id=main_group_id, text=complete_text, parse_mode='Markdown')
    
    # Save to completed deals history
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""INSERT INTO completed_deals 
              (room_num, seller_username, buyer_username, start_time, end_time, status, reason, cancelled_by) 
              VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (escrow['room_num'], escrow['user1_username'], escrow['user2_username'],
               escrow['start_time'], time.time(), "completed", "Deal successful", "Admin"))
    conn.commit()
    conn.close()
    
    # Free room and delete from active escrows
    free_room(room_id)
    conn = sqlite3.connect(DB_NAME)
    conn.execute("DELETE FROM active_escrows WHERE room_id=?", (room_id,))
    conn.commit()
    conn.close()
    
    await msg.reply_text("✅ Deal completed! Room is now free.")

# =========================
# RUN BOT
# =========================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot Running"

async def run_bot():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("escrow", escrow_command))
    
    # Message filters for main group
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_messages), group=1)
    
    # Chat member handlers
    app.add_handler(ChatMemberHandler(handle_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    app.add_handler(ChatMemberHandler(handle_security, ChatMemberHandler.CHAT_MEMBER))
    
    # Role selection callback
    app.add_handler(CallbackQueryHandler(role_callback, pattern="^role_"))
    
    # Cancel callback
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern="^cancel_"))
    
    # Cancel and complete commands in escrow rooms
    escrow_room_ids = [room["room_id"] for room in ROOMS]
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=escrow_room_ids), cancel_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=escrow_room_ids), complete_command))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "chat_member", "callback_query"])
    while True:
        await asyncio.sleep(100)

if __name__ == "__main__":
    threading.Thread(target=lambda: asyncio.run(run_bot()), daemon=True).start()
    flask_app.run(host="0.0.0.0", port=PORT)
