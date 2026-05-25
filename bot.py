import re
import os
import time
import sqlite3
import logging
import threading
import asyncio
from datetime import datetime, timedelta

from flask import Flask
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
    ChatMemberHandler
)

from telegram.constants import ChatMemberStatus

# =========================
# LOAD ENV
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID_INPUT = os.getenv("GROUP_ID")  # @Escrowspartans
ADMIN_ID = int(os.getenv("ADMIN_ID", "6722137021"))
ADMIN_USERNAME = "@crypto_8099"
ADMIN_WALLET = os.getenv("ADMIN_WALLET", "0xYourAdminWalletAddressHere")
PORT = int(os.getenv("PORT", 10000))
RESTRICTION_HOURS = 24

# =========================
# ESCROW ROOMS
# =========================

ROOMS = [
    {
        "room_num": 1,
        "room_id": -1003970953090,
        "invite_link": "https://t.me/+BHJL7ayHNJw3OTE1",
        "busy": False
    },
    {
        "room_num": 2,
        "room_id": -1003766531525,
        "invite_link": "https://t.me/+FbR1VOqqXqswYjJl",
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
# DATABASE
# =========================

DB_NAME = "escrow.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Users table with restriction
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            join_time REAL,
            bot_started BOOLEAN DEFAULT 0,
            restriction_end_time REAL
        )
    """)

    # Active escrows table
    c.execute("""
        CREATE TABLE IF NOT EXISTS active_escrows (
            seller_username TEXT,
            buyer_username TEXT,
            seller_id INTEGER,
            buyer_id INTEGER,
            room_id INTEGER,
            room_num INTEGER,
            start_time REAL,
            invite_link TEXT
        )
    """)

    # Escrow history table
    c.execute("""
        CREATE TABLE IF NOT EXISTS escrow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_username TEXT,
            buyer_username TEXT,
            seller_id INTEGER,
            buyer_id INTEGER,
            room_num INTEGER,
            start_time REAL,
            end_time REAL,
            status TEXT,
            duration_seconds INTEGER,
            completed_by TEXT
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized")

def add_or_update_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    existing = c.fetchone()
    
    if existing:
        c.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (username, first_name, user_id))
    else:
        restriction_end = time.time() + (RESTRICTION_HOURS * 3600)
        c.execute("""
            INSERT INTO users (user_id, username, first_name, join_time, bot_started, restriction_end_time)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (user_id, username, first_name, time.time(), restriction_end))
        logger.info(f"New user added: {username}({user_id}) - Restricted until {datetime.fromtimestamp(restriction_end)}")
    
    conn.commit()
    conn.close()

def user_can_send_message(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT restriction_end_time FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return False
    
    return time.time() >= result[0]

def mark_bot_started(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET bot_started=1, restriction_end_time=? WHERE user_id=?", (time.time(), user_id))
    conn.commit()
    conn.close()
    logger.info(f"User {user_id} marked as bot_started, restriction removed")

def get_restriction_time(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT restriction_end_time FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def has_active_escrow(username):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM active_escrows WHERE seller_username=? OR buyer_username=?", (username, username))
    result = c.fetchone()
    conn.close()
    return result is not None

def add_active_escrow(seller_username, buyer_username, seller_id, buyer_id, room_id, room_num, start_time, invite_link):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO active_escrows VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (seller_username, buyer_username, seller_id, buyer_id, room_id, room_num, start_time, invite_link))
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
        "seller_id": result[2],
        "buyer_id": result[3],
        "room_id": result[4],
        "room_num": result[5],
        "start_time": result[6],
        "invite_link": result[7]
    }

def remove_escrow(room_id, status, end_time, duration, completed_by=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM active_escrows WHERE room_id=?", (room_id,))
    escrow = c.fetchone()
    if escrow:
        c.execute("""
            INSERT INTO escrow_history (seller_username, buyer_username, seller_id, buyer_id, room_num, start_time, end_time, status, duration_seconds, completed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (escrow[0], escrow[1], escrow[2], escrow[3], escrow[5], escrow[6], end_time, status, duration, completed_by))
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
# MESSAGE VALIDATION
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

ALLOWED_MESSAGES = ["dm", "hi", "hello", "check dm", "done", "paid", "sent", "ok", "yes", "available", "/start"]

# =========================
# WELCOME & RESTRICT NEW MEMBERS
# =========================

async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when new members join the group"""
    
    chat_member = update.chat_member
    
    if not chat_member:
        return
    
    # Check if user joined (status changed from LEFT to MEMBER)
    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status
    
    if new_status == ChatMemberStatus.MEMBER and old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, None]:
        
        user = chat_member.new_chat_member.user
        user_id = user.id
        username = user.username or user.first_name
        first_name = user.first_name
        chat_id = chat_member.chat.id
        
        # Add to database
        add_or_update_user(user_id, username, first_name)
        
        # Restrict user (mute for 24 hours)
        restriction_end = time.time() + (RESTRICTION_HOURS * 3600)
        until_date = datetime.fromtimestamp(restriction_end)
        
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_date
            )
            logger.info(f"🔇 Restricted {username}({user_id}) for {RESTRICTION_HOURS}h")
        except Exception as e:
            logger.error(f"Failed to restrict: {e}")
        
        # Welcome message with start button
        keyboard = [[InlineKeyboardButton("✅ Verify & Start Bot", url=f"https://t.me/{context.bot.username}?start=verify")]]
        
        welcome_text = (
            f"🔒 **WELCOME {first_name}!** 🔒\n\n"
            f"⏰ **You are restricted for {RESTRICTION_HOURS} hours!**\n"
            f"📵 You cannot send messages until you verify.\n\n"
            f"✅ **To remove restriction immediately:**\n"
            f"   1️⃣ Click **Verify & Start Bot** button below\n"
            f"   2️⃣ Press /start\n"
            f"   3️⃣ Restriction lifted instantly!\n\n"
            f"💰 Admin: {ADMIN_USERNAME}"
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        
        # Also send DM
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔒 **You joined @Escrowspartans**\n\nClick here to verify: t.me/{context.bot.username}?start\n\nYour ID: `{user_id}`",
                parse_mode='Markdown'
            )
        except:
            pass

# =========================
# START COMMAND - REMOVE RESTRICTION
# =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    
    user = msg.from_user
    user_id = user.id
    username = user.username or user.first_name
    
    # Save to database and mark as started
    add_or_update_user(user_id, username, user.first_name)
    mark_bot_started(user_id)
    
    # Get main group ID
    main_group_id = None
    try:
        if str(GROUP_ID_INPUT).lstrip('-').isdigit():
            main_group_id = int(GROUP_ID_INPUT)
        else:
            chat = await context.bot.get_chat(GROUP_ID_INPUT)
            main_group_id = chat.id
    except:
        pass
    
    # Remove restriction in main group
    if main_group_id:
        try:
            await context.bot.restrict_chat_member(
                chat_id=main_group_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True
                )
            )
            logger.info(f"✅ Restriction removed for {username}({user_id})")
            
            # Send success message in group
            await context.bot.send_message(
                chat_id=main_group_id,
                text=f"✅ **@{username}** has been verified! They can now participate in trades. 🎉"
            )
        except Exception as e:
            logger.error(f"Failed to remove restriction: {e}")
    
    # Send DM response
    is_admin = (user_id == ADMIN_ID)
    
    await msg.reply_text(
        f"✅ **Verification Successful!**\n\n"
        f"👤 @{username}\n"
        f"🆔 `{user_id}`\n\n"
        f"🔓 Your restriction has been **REMOVED**!\n"
        f"✅ You can now send messages in the group.\n\n"
        f"📌 **Commands:**\n"
        f"/escrow @username - Start a trade\n\n"
        f"💰 Admin Wallet: `{ADMIN_WALLET}`",
        parse_mode='Markdown'
    )

# =========================
# MESSAGE FILTER WITH RESTRICTION
# =========================

async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    
    # Only check in main group
    chat = msg.chat
    user = msg.from_user
    user_id = user.id
    
    # Check if this is main group
    main_group_id = None
    try:
        if str(GROUP_ID_INPUT).lstrip('-').isdigit():
            main_group_id = int(GROUP_ID_INPUT)
        else:
            chat_info = await context.bot.get_chat(GROUP_ID_INPUT)
            main_group_id = chat_info.id
    except:
        pass
    
    if chat.id != main_group_id:
        return
    
    # Admin bypass
    if user_id == ADMIN_ID:
        return
    
    # Check if user can send messages
    if not user_can_send_message(user_id):
        restriction_end = get_restriction_time(user_id)
        if restriction_end:
            remaining = restriction_end - time.time()
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            
            await msg.delete()
            await msg.reply_text(
                f"🔴 **You are restricted!** 🔴\n\n"
                f"⏰ Remaining: {hours}h {minutes}m\n\n"
                f"✅ **Remove restriction:**\n"
                f"1️⃣ Start @{context.bot.username}\n"
                f"2️⃣ Press /start\n\n"
                f"⚠️ Anti-scam protection."
            )
            return
    
    # Check message format for trade posts
    if msg.text.lower().startswith(('#buying', '#selling')):
        if not is_valid(msg.text):
            await msg.delete()
            await msg.reply_text("❌ Invalid format!\n\nUse:\n#buying or #selling\nChain: BEP20\nAmount[USDT]: ?\nAmount[INR]: ?\nRate[INR/USDT]: ?\nPayment method: ?")

# =========================
# ESCROW COMMAND
# =========================

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    
    # Check in main group
    main_group_id = None
    try:
        if str(GROUP_ID_INPUT).lstrip('-').isdigit():
            main_group_id = int(GROUP_ID_INPUT)
        else:
            chat_info = await context.bot.get_chat(GROUP_ID_INPUT)
            main_group_id = chat_info.id
    except:
        pass
    
    if msg.chat.id != main_group_id:
        await msg.reply_text("❌ Use /escrow in main group only!")
        return
    
    if len(context.args) < 1:
        await msg.reply_text("❌ Usage: /escrow @username")
        return
    
    seller = msg.from_user
    seller_username = seller.username.lower() if seller.username else None
    
    if not seller_username:
        await msg.reply_text("❌ Set a username first!")
        return
    
    buyer_username = context.args[0].replace("@", "").lower()
    
    if seller_username == buyer_username:
        await msg.reply_text("❌ Cannot escrow with yourself!")
        return
    
    room = get_free_room()
    if not room:
        await msg.reply_text("❌ All rooms busy! Please wait.")
        return
    
    invite_link = room.get("invite_link")
    start_time = time.time()
    
    add_active_escrow(
        seller_username, buyer_username, seller.id, 0,
        room["room_id"], room["room_num"], start_time, invite_link
    )
    
    await msg.reply_text(
        f"🔒 **ESCROW ROOM #{room['room_num']}**\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"🔗 {invite_link}\n\n"
        f"💰 Admin Wallet: `{ADMIN_WALLET}`",
        parse_mode='Markdown'
    )
    
    # Send to escrow room
    try:
        await context.bot.send_message(
            chat_id=room["room_id"],
            text=(
                f"🔒 **ESCROW STARTED - Room #{room['room_num']}**\n\n"
                f"Seller: @{seller_username} (ID: `{seller.id}`)\n"
                f"Buyer: @{buyer_username}\n\n"
                f"**PROCESS:**\n"
                f"1️⃣ Seller sends USDT to admin\n"
                f"2️⃣ Buyer pays seller (INR)\n"
                f"3️⃣ Admin releases USDT\n\n"
                f"💰 Admin Wallet: `{ADMIN_WALLET}`"
            ),
            parse_mode='Markdown'
        )
    except:
        pass

# =========================
# COMPLETE COMMAND
# =========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    
    escrow = get_active_escrow_by_room(msg.chat_id)
    if not escrow:
        await msg.reply_text("❌ No active escrow!")
        return
    
    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    
    remove_escrow(msg.chat_id, "completed", end_time, duration, "Admin")
    free_room(msg.chat_id)
    
    await msg.reply_text(f"✅ **DEAL COMPLETED!**\nDuration: {duration//60}m {duration%60}s")
    
    # Notify main group
    main_group_id = None
    try:
        if str(GROUP_ID_INPUT).lstrip('-').isdigit():
            main_group_id = int(GROUP_ID_INPUT)
        else:
            chat_info = await context.bot.get_chat(GROUP_ID_INPUT)
            main_group_id = chat_info.id
    except:
        pass
    
    if main_group_id:
        await context.bot.send_message(
            main_group_id,
            f"✅ **DEAL COMPLETED** in Room #{escrow['room_num']}!\n"
            f"Seller: @{escrow['seller_username']}\n"
            f"Buyer: @{escrow['buyer_username']}"
        )

# =========================
# CANCEL COMMAND
# =========================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    
    escrow = get_active_escrow_by_room(msg.chat_id)
    if not escrow:
        await msg.reply_text("❌ No active escrow!")
        return
    
    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    
    remove_escrow(msg.chat_id, "cancelled", end_time, duration, "Admin")
    free_room(msg.chat_id)
    
    await msg.reply_text(f"❌ **DEAL CANCELLED!**")

# =========================
# ADMIN COMMANDS
# =========================

async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user.id == ADMIN_ID:
        await update.message.reply_text(f"🆔 Chat ID: `{update.message.chat_id}`", parse_mode='Markdown')

async def show_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT room_num, seller_username, buyer_username FROM active_escrows")
    results = c.fetchall()
    conn.close()
    
    if results:
        text = "Active Escrows:\n"
        for r in results:
            text += f"Room {r[0]}: @{r[1]} → @{r[2]}\n"
        await update.message.reply_text(text)
    else:
        await update.message.reply_text("No active escrows.")

# =========================
# FLASK APP
# =========================

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Escrow Bot Running!"

@flask_app.route("/health")
def health():
    return {"status": "ok"}

# =========================
# START BOT
# =========================

async def start_bot():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("show", show_command))
    
    # Message filter
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_msg))
    
    # NEW MEMBER HANDLER - This is the key!
    app.add_handler(ChatMemberHandler(handle_new_members, ChatMemberHandler.CHAT_MEMBER))
    
    logger.info("Bot started...")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep running
    while True:
        await asyncio.sleep(100)

def run_bot():
    asyncio.run(start_bot())

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    logger.info("Starting bot...")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    flask_app.run(host="0.0.0.0", port=PORT)
