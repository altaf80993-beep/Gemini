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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
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
GROUP_ID_INPUT = os.getenv("GROUP_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6722137021"))
ADMIN_USERNAME = "@crypto_8099"
ADMIN_WALLET = os.getenv("ADMIN_WALLET", "0xYourAdminWalletAddressHere")
PORT = int(os.getenv("PORT", 10000))
RESTRICTION_HOURS = int(os.getenv("RESTRICTION_HOURS", "24"))  # 24 hours restriction

GROUP_ID = GROUP_ID_INPUT

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
    {
        "room_num": 3,
        "room_id": -1004123456789,
        "invite_link": "https://t.me/+YourRoom3Link",
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

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            join_time REAL,
            bot_started BOOLEAN DEFAULT 0,
            can_send_message BOOLEAN DEFAULT 0,
            restriction_end_time REAL
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized")

def add_or_update_user(user_id, username, first_name, join_time=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Check if user exists
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    existing = c.fetchone()
    
    if existing:
        # Update username if changed
        c.execute("""
            UPDATE users 
            SET username=?, first_name=?
            WHERE user_id=?
        """, (username, first_name, user_id))
    else:
        # New user - set restriction end time (24 hours from join)
        if join_time is None:
            join_time = time.time()
        restriction_end = join_time + (RESTRICTION_HOURS * 3600)
        
        c.execute("""
            INSERT INTO users (user_id, username, first_name, join_time, bot_started, can_send_message, restriction_end_time)
            VALUES (?, ?, ?, ?, 0, 0, ?)
        """, (user_id, username, first_name, join_time, restriction_end))
        logger.info(f"New user added: {username}({user_id}) - Restricted until {datetime.fromtimestamp(restriction_end)}")
    
    conn.commit()
    conn.close()

def user_can_send_message(user_id):
    """Check if user can send messages in main group"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT restriction_end_time, bot_started FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return False
    
    restriction_end_time, bot_started = result
    current_time = time.time()
    
    # User can send if:
    # 1. Restriction time has passed OR
    # 2. Bot started AND restriction time passed (both conditions)
    can_send = (current_time >= restriction_end_time)
    
    if not can_send:
        remaining_hours = int((restriction_end_time - current_time) // 3600)
        remaining_minutes = int(((restriction_end_time - current_time) % 3600) // 60)
        logger.info(f"User {user_id} restricted: {remaining_hours}h {remaining_minutes}m remaining")
    
    return can_send

def mark_bot_started(user_id):
    """Mark that user has started the bot"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET bot_started=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    logger.info(f"User {user_id} marked as bot_started")

def get_user_info(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username, first_name, restriction_end_time, bot_started FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result:
        return {
            "username": result[0],
            "first_name": result[1],
            "restriction_end_time": result[2],
            "bot_started": result[3]
        }
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

def add_active_escrow(seller_username, buyer_username, seller_id, buyer_id, room_id, room_num, start_time, invite_link):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO active_escrows VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (seller_username, buyer_username, seller_id, buyer_id, room_id, room_num, start_time, invite_link))
    conn.commit()
    conn.close()
    logger.info(f"✅ Active escrow added: Room {room_num}")

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
        logger.info(f"✅ Escrow removed: Room {escrow[5]} | Status: {status}")
    conn.commit()
    conn.close()

def get_free_room():
    for room in ROOMS:
        if not room["busy"]:
            room["busy"] = True
            logger.info(f"📌 Room {room['room_num']} is now BUSY")
            return room
    logger.warning("No free rooms available!")
    return None

def free_room(room_id):
    for room in ROOMS:
        if room["room_id"] == room_id:
            room["busy"] = False
            logger.info(f"📌 Room {room['room_num']} is now FREE")
            break

# =========================
# MESSAGE FORMAT
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

ALLOWED_MESSAGES = ["dm", "hi", "hello", "check dm", "done", "paid", "sent", "ok", "yes", "available"]

# =========================
# GET USER ID
# =========================

async def get_user_id_from_database(username):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username=?", (username,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

# =========================
# WELCOME NEW MEMBERS + RESTRICT
# =========================

async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When new member joins, restrict them and send welcome message"""
    
    chat_member_update = update.chat_member
    
    if not chat_member_update:
        return
    
    # Check if user joined
    if chat_member_update.new_chat_member.status == ChatMemberStatus.MEMBER and \
       chat_member_update.old_chat_member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, None]:
        
        user = chat_member_update.new_chat_member.user
        user_id = user.id
        username = user.username or user.first_name
        first_name = user.first_name
        join_time = time.time()
        
        # Add user to database with restriction
        add_or_update_user(user_id, username, first_name, join_time)
        
        # Restrict user in group (mute for 24 hours)
        try:
            # Get restriction end time
            restriction_end = join_time + (RESTRICTION_HOURS * 3600)
            until_date = datetime.fromtimestamp(restriction_end)
            
            # Restrict user from sending messages
            await context.bot.restrict_chat_member(
                chat_id=chat_member_update.chat.id,
                user_id=user_id,
                permissions=telegram.ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=until_date
            )
            logger.info(f"🔇 User {username}({user_id}) restricted for {RESTRICTION_HOURS} hours")
            
        except Exception as e:
            logger.error(f"Failed to restrict user: {e}")
        
        # Create inline keyboard
        keyboard = [
            [InlineKeyboardButton("🚀 Start Bot & Verify", url=f"https://t.me/{context.bot.username}?start=welcome")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send welcome message with restriction info
        welcome_text = (
            f"🔒 **WELCOME {first_name}!** 🔒\n\n"
            f"👋 Welcome to **escrow spartans group**!\n\n"
            f"⏰ **IMPORTANT - ANTI-SCAM PROTECTION:**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔴 **You are restricted for {RESTRICTION_HOURS} hours!** 🔴\n"
            f"📵 **You CANNOT send messages in this group until:**\n"
            f"   {until_date.strftime('%Y-%m-%d %H:%M:%S')} IST\n\n"
            f"✅ **To remove restriction faster:**\n"
            f"   1️⃣ Click **Start Bot & Verify** button below\n"
            f"   2️⃣ Press /start in the bot\n"
            f"   3️⃣ Your restriction will end immediately!\n\n"
            f"⚠️ **Why this rule?**\n"
            f"   • Prevents new account scams\n"
            f"   • Ensures all traders are verified\n"
            f"   • Protects community from fraud\n\n"
            f"💰 **Need to trade urgently?** Contact admin: {ADMIN_USERNAME}"
        )
        
        try:
            await context.bot.send_message(
                chat_id=chat_member_update.chat.id,
                text=welcome_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            logger.info(f"Welcome message sent to {username}({user_id})")
        except Exception as e:
            logger.error(f"Failed to send welcome message: {e}")
        
        # Send DM to user
        try:
            dm_text = (
                f"🔒 **You are restricted in Crypto India Escrow** 🔒\n\n"
                f"⏰ **Restriction period:** {RESTRICTION_HOURS} hours\n"
                f"📵 **You cannot send messages until you verify.**\n\n"
                f"✅ **To verify and remove restriction:**\n"
                f"   1️⃣ Click here: t.me/{context.bot.username}?start=verify\n"
                f"   2️⃣ Press /start\n"
                f"   3️⃣ Your restriction will be lifted immediately!\n\n"
                f"🆔 **Your User ID:** `{user_id}`\n\n"
                f"⚠️ This is to protect the community from scammers.\n"
                f"Thank you for understanding!"
            )
            await context.bot.send_message(
                chat_id=user_id,
                text=dm_text,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.warning(f"Could not send DM to {username}: {e}")

# =========================
# START COMMAND - REMOVES RESTRICTION
# =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    
    user = msg.from_user
    user_id = user.id
    username = user.username or user.first_name
    first_name = user.first_name
    
    # Check if user exists in database
    user_info = get_user_info(user_id)
    
    # Add or update user
    add_or_update_user(user_id, username, first_name, time.time())
    mark_bot_started(user_id)
    
    # Remove restriction in main group
    main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
    
    try:
        # Give full permissions back
        await context.bot.restrict_chat_member(
            chat_id=main_group_id,
            user_id=user_id,
            permissions=telegram.ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        logger.info(f"✅ Restriction removed for {username}({user_id})")
        
        # Send confirmation in group
        await context.bot.send_message(
            chat_id=main_group_id,
            text=f"✅ **{first_name}** has been verified! They can now send messages. 🎉"
        )
        
    except Exception as e:
        logger.error(f"Failed to remove restriction: {e}")
    
    # Check if admin
    is_admin = (user_id == ADMIN_ID)
    admin_text = "\n\n👑 **You are an Admin!**" if is_admin else ""
    
    # Send welcome message in DM
    welcome_text = (
        f"✅ **Verification Successful!** ✅\n\n"
        f"👤 User: @{username}\n"
        f"🆔 Your ID: `{user_id}`\n"
        f"📅 Verified at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{admin_text}\n\n"
        f"🔓 **Your restriction has been REMOVED!**\n"
        f"✅ You can now send messages in the main group.\n\n"
        f"📌 **Available Commands:**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔹 `/escrow @username` - Start a new escrow\n"
        f"\n"
        f"📝 **Inside Escrow Room:**\n"
        f"🔹 `/confirm_paid` - Confirm USDT sent (Seller)\n\n"
        f"⚠️ **Admin:** {ADMIN_USERNAME}\n"
        f"💰 **Admin Wallet:** `{ADMIN_WALLET}`"
    )
    
    await msg.reply_text(welcome_text, parse_mode='Markdown')
    logger.info(f"User {username}({user_id}) started the bot and restriction removed")

# =========================
# MESSAGE FILTER WITH 24-HOUR RESTRICTION
# =========================

async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat = msg.chat
    chat_username = (chat.username or "").lower()
    target_group = str(GROUP_ID_INPUT).replace("@", "").lower()
    main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID

    # Only check in main group
    if chat_username != target_group and str(chat.id) != str(main_group_id):
        return

    user = msg.from_user
    user_id = user.id
    
    # Admin is always allowed
    if user_id == ADMIN_ID:
        return
    
    # Check if user can send messages (24-hour restriction check)
    can_send = user_can_send_message(user_id)
    
    if not can_send:
        # Get remaining time
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT restriction_end_time FROM users WHERE user_id=?", (user_id,))
        result = c.fetchone()
        conn.close()
        
        if result:
            remaining = result[0] - time.time()
            remaining_hours = int(remaining // 3600)
            remaining_minutes = int((remaining % 3600) // 60)
            
            await msg.delete()
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=(
                    f"🔴 **You are restricted from sending messages!** 🔴\n\n"
                    f"⏰ **Remaining time:** {remaining_hours}h {remaining_minutes}m\n\n"
                    f"✅ **To remove restriction immediately:**\n"
                    f"   1️⃣ Start the bot: @{context.bot.username}\n"
                    f"   2️⃣ Press /start\n"
                    f"   3️⃣ Restriction will be lifted!\n\n"
                    f"⚠️ This is an anti-scam measure to protect everyone."
                )
            )
            logger.info(f"Blocked message from restricted user {user_id}")
            return
    
    # Check for admin/owner
    try:
        member = await context.bot.get_chat_member(msg.chat_id, user_id)
        if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return
    except Exception as e:
        logger.error(e)

    # Check message format for buying/selling posts
    text_lower = msg.text.strip().lower()
    if text_lower in ALLOWED_MESSAGES:
        return

    if not is_valid(msg.text):
        try:
            await msg.delete()
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text="❌ Invalid Post Format!\n\nUse format:\n#buying or #selling\nChain: BEP20\nAmount[USDT]: ?\nAmount[INR]: ?\nRate[INR/USDT]: ?\nPayment method: ?"
            )
        except Exception as e:
            logger.error(e)

# =========================
# ESCROW COMMAND
# =========================

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    chat_id = msg.chat_id
    main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
    
    if chat_id != main_group_id:
        await msg.reply_text("❌ /escrow command can only be used in the main group!")
        return

    if len(context.args) < 1:
        await msg.reply_text("❌ Usage:\n\n/escrow @username")
        return

    seller = msg.from_user
    
    # Check if seller is restricted
    if not user_can_send_message(seller.id) and seller.id != ADMIN_ID:
        await msg.reply_text("❌ You are restricted! Please start the bot first: @{context.bot.username}")
        return

    if not seller.username:
        await msg.reply_text("❌ Please set Telegram username first!")
        return

    seller_username = seller.username.lower()
    seller_id = seller.id
    buyer_username = context.args[0].replace("@", "").lower()

    if seller_username == buyer_username:
        await msg.reply_text("❌ You cannot escrow with yourself!")
        return

    if has_active_escrow(seller_username):
        await msg.reply_text(f"❌ @{seller_username} already has active escrow!")
        return

    if has_active_escrow(buyer_username):
        await msg.reply_text(f"❌ @{buyer_username} already has active escrow!")
        return

    room = get_free_room()
    if not room:
        await msg.reply_text("❌ All escrow rooms are busy! Please wait.")
        return

    room_id = room["room_id"]
    
    # Get buyer ID from database
    buyer_id = await get_user_id_from_database(buyer_username)
    
    if not buyer_id:
        await msg.reply_text(
            f"⚠️ **Warning:** @{buyer_username} hasn't started the bot yet!\n\n"
            f"Please ask them to click the **Start Bot** button they received when joining.\n\n"
            f"They must start the bot before escrow can proceed properly.\n\n"
            f"The escrow has been created, but will work normally once they verify."
        )
        buyer_id = 0

    start_time = time.time()
    
    # Get invite link
    invite_link = room.get("invite_link")
    if not invite_link:
        try:
            link_obj = await context.bot.create_chat_invite_link(chat_id=room_id, member_limit=2, expire_date=0)
            invite_link = link_obj.invite_link
            room["invite_link"] = invite_link
        except:
            invite_link = room["invite_link"]

    add_active_escrow(seller_username, buyer_username, seller_id, buyer_id, room["room_id"], room["room_num"], start_time, invite_link)

    # ESCROW ROOM MESSAGE
    try:
        await context.bot.send_message(
            chat_id=room["room_id"],
            text=(
                f"🔒 **ESCROW STARTED**\n\n"
                f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n"
                f"👤 Buyer: @{buyer_username} (ID: `{buyer_id if buyer_id else 'Not Verified'}`)\n"
                f"🆔 Room #{room['room_num']}\n\n"
                f"📝 **PROCESS:**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"**STEP 1️⃣** - Seller sends USDT to Admin\n"
                f"**STEP 2️⃣** - Buyer pays Seller (INR)\n"
                f"**STEP 3️⃣** - Admin releases USDT to Buyer\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 **Admin Wallet:** `{ADMIN_WALLET}`\n\n"
                f"⚠️ Only admin can complete/cancel!"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Room message error: {e}")

    # MAIN GROUP MESSAGE
    await msg.reply_text(
        f"🔒 **ESCROW ROOM #{room['room_num']}**\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"🔗 **Join Link:**\n{invite_link}"
    )

    logger.info(f"✅ Escrow created: Room #{room['room_num']}")

# =========================
# CONFIRM PAID COMMAND
# =========================

async def confirm_paid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    
    room_id = msg.chat_id
    user = msg.from_user
    
    escrow = get_active_escrow_by_room(room_id)
    if not escrow:
        await msg.reply_text("❌ No active escrow in this room!")
        return
    
    if user.id != escrow['seller_id']:
        await msg.reply_text("❌ Only seller can use /confirm_paid")
        return
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"💰 **USDT CONFIRMATION**\n\n"
                f"Seller: @{escrow['seller_username']}\n"
                f"Buyer: @{escrow['buyer_username']}\n"
                f"Room: #{escrow['room_num']}\n\n"
                f"✅ Seller confirmed USDT sent!\n"
                f"Use /complete in room ID: `{room_id}`"
            ),
            parse_mode='Markdown'
        )
        await msg.reply_text("✅ Admin notified! Deal will be completed shortly.")
    except Exception as e:
        logger.error(f"Confirm paid error: {e}")

# =========================
# COMPLETE COMMAND
# =========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    room_id = msg.chat_id
    user = msg.from_user
    escrow = get_active_escrow_by_room(room_id)

    if not escrow:
        await msg.reply_text("❌ No active escrow in this room!")
        return
    
    if user.id != ADMIN_ID:
        await msg.reply_text(f"❌ Only admin can complete deals!")
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    remove_escrow(room_id, "completed", end_time, duration, completed_by="Admin")
    free_room(room_id)

    await msg.reply_text(f"✅ **DEAL COMPLETED!**\nDuration: {minutes}m {seconds}s")
    
    # Main group notification
    main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
    try:
        await context.bot.send_message(
            chat_id=main_group_id,
            text=f"✅ **DEAL COMPLETED** in Room #{escrow['room_num']}!\nSeller: @{escrow['seller_username']}\nBuyer: @{escrow['buyer_username']}\nDuration: {minutes}m {seconds}s"
        )
    except Exception as e:
        logger.error(f"Failed to send completion notice: {e}")

# =========================
# CANCEL COMMAND
# =========================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    room_id = msg.chat_id
    user = msg.from_user
    escrow = get_active_escrow_by_room(room_id)

    if not escrow:
        await msg.reply_text("❌ No active escrow in this room!")
        return
    
    if user.id != ADMIN_ID:
        await msg.reply_text(f"❌ Only admin can cancel deals!")
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    remove_escrow(room_id, "cancelled", end_time, duration, completed_by="Admin")
    free_room(room_id)
    await msg.reply_text(f"❌ **DEAL CANCELLED** by admin!\nDuration: {minutes}m {seconds}s")

# =========================
# GET ID COMMANDS
# =========================

async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    await msg.reply_text(f"🆔 Chat ID: `{msg.chat_id}`", parse_mode='Markdown')

async def show_escrows_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT room_num, seller_username, buyer_username FROM active_escrows")
    results = c.fetchall()
    conn.close()
    
    if results:
        text = "📋 **Active Escrows:**\n"
        for r in results:
            text += f"Room #{r[0]}: @{r[1]} → @{r[2]}\n"
        await msg.reply_text(text)
    else:
        await msg.reply_text("No active escrows.")

# =========================
# FLASK APP
# =========================

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Escrow Bot Running!"

@flask_app.route("/health")
def health():
    return {"status": "ok", "timestamp": time.time()}

# =========================
# RESOLVE GROUP ID
# =========================

async def resolve_group_id(app):
    global GROUP_ID
    try:
        if str(GROUP_ID_INPUT).lstrip("-").isdigit():
            GROUP_ID = int(GROUP_ID_INPUT)
            logger.info(f"Using numeric group ID: {GROUP_ID}")
            return
        chat = await app.bot.get_chat(GROUP_ID_INPUT)
        GROUP_ID = chat.id
        logger.info(f"Resolved group ID: {GROUP_ID}")
    except Exception as e:
        logger.error(f"Group resolve error: {e}")

# =========================
# START BOT
# =========================

async def start_bot():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("confirm_paid", confirm_paid_command))
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("show", show_escrows_command))
    
    # Message filter
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_msg))
    
    # Welcome new members with restriction
    app.add_handler(ChatMemberHandler(welcome_new_members, ChatMemberHandler.CHAT_MEMBER))

    logger.info("Bot started...")
    await app.initialize()
    await app.start()
    await resolve_group_id(app)
    await app.updater.start_polling()

    while True:
        await asyncio.sleep(100)

# =========================
# RUN BOT THREAD
# =========================

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_bot())

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    # Need to import telegram for ChatPermissions
    import telegram
    logger.info("Starting services...")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    flask_app.run(host="0.0.0.0", port=PORT)
