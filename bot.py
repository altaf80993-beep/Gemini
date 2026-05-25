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
    Application, MessageHandler, CommandHandler, ContextTypes,
    filters, ChatMemberHandler, CallbackQueryHandler
)
from telegram.constants import ChatMemberStatus

# =========================
# LOAD ENV
# =========================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID_INPUT = os.getenv("GROUP_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6722137021"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@crypto_8099")
ADMIN_WALLET = os.getenv("ADMIN_WALLET", "0xYourAdminWalletAddressHere")
PORT = int(os.getenv("PORT", 10000))
RESTRICTION_HOURS = int(os.getenv("RESTRICTION_HOURS", "24"))

# =========================
# ESCROW ROOMS (2 for testing, you can add more)
# =========================
ROOMS = [
    {"room_num": 1, "room_id": -1003970953090, "invite_link": "https://t.me/+BHJL7ayHNJw3OTE1", "busy": False},
    {"room_num": 2, "room_id": -1003766531525, "invite_link": "https://t.me/+FbR1VOqqXqswYjJl", "busy": False},
]

# =========================
# LOGGING
# =========================
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# DATABASE
# =========================
DB_NAME = "escrow.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        join_time REAL,
        verified BOOLEAN DEFAULT 0,
        restriction_end_time REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS active_escrows (
        room_id INTEGER PRIMARY KEY,
        room_num INTEGER,
        seller_username TEXT,
        seller_id INTEGER,
        buyer_username TEXT,
        buyer_id INTEGER,
        seller_confirmed BOOLEAN DEFAULT 0,
        admin_proceeded BOOLEAN DEFAULT 0,
        start_time REAL,
        invite_link TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS escrow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_num INTEGER,
        seller_username TEXT,
        seller_id INTEGER,
        buyer_username TEXT,
        buyer_id INTEGER,
        start_time REAL,
        end_time REAL,
        status TEXT,
        duration_seconds INTEGER
    )""")
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def add_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    restriction_end = time.time() + (RESTRICTION_HOURS * 3600)
    c.execute("INSERT OR REPLACE INTO users (user_id, username, first_name, join_time, verified, restriction_end_time) VALUES (?, ?, ?, ?, 0, ?)",
              (user_id, username, first_name, time.time(), restriction_end))
    conn.commit()
    conn.close()

def verify_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE users SET verified=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username, verified, restriction_end_time FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result

def can_send_message(user_id):
    result = get_user(user_id)
    if not result:
        return False
    username, verified, restriction_end = result
    return time.time() >= restriction_end

def get_restriction_time_left(user_id):
    result = get_user(user_id)
    if not result:
        return 0
    remaining = result[2] - time.time()
    return max(0, remaining)

def add_active_escrow(room_id, room_num, seller_username, seller_id, buyer_username, buyer_id, invite_link):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO active_escrows (room_id, room_num, seller_username, seller_id, buyer_username, buyer_id, seller_confirmed, admin_proceeded, start_time, invite_link) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
              (room_id, room_num, seller_username, seller_id, buyer_username, buyer_id, time.time(), invite_link))
    conn.commit()
    conn.close()

def get_active_escrow(room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM active_escrows WHERE room_id=?", (room_id,))
    result = c.fetchone()
    conn.close()
    if not result:
        return None
    return {
        "room_id": result[0], "room_num": result[1], "seller_username": result[2],
        "seller_id": result[3], "buyer_username": result[4], "buyer_id": result[5],
        "seller_confirmed": result[6], "admin_proceeded": result[7],
        "start_time": result[8], "invite_link": result[9]
    }

def update_seller_confirmed(room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE active_escrows SET seller_confirmed=1 WHERE room_id=?", (room_id,))
    conn.commit()
    conn.close()

def update_admin_proceeded(room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE active_escrows SET admin_proceeded=1 WHERE room_id=?", (room_id,))
    conn.commit()
    conn.close()

def remove_active_escrow(room_id, status, end_time, duration):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM active_escrows WHERE room_id=?", (room_id,))
    escrow = c.fetchone()
    if escrow:
        c.execute("INSERT INTO escrow_history (room_num, seller_username, seller_id, buyer_username, buyer_id, start_time, end_time, status, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (escrow[1], escrow[2], escrow[3], escrow[4], escrow[5], escrow[8], end_time, status, duration))
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

def update_username_in_db(user_id, new_username):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    if result and result[0] != new_username:
        old_username = result[0]
        c.execute("UPDATE users SET username=? WHERE user_id=?", (new_username, user_id))
        conn.commit()
        conn.close()
        return old_username
    conn.close()
    return None

# =========================
# MESSAGE VALIDATION
# =========================
def is_valid_trade_post(text: str) -> bool:
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

ALLOWED_WORDS = [
    "hi", "hello", "hey", "dm", "check dm", "done", "paid", "sent",
    "received", "ok", "yes", "no", "available", "confirm", "pending",
    "wait", "waiting", "thanks", "thank you", "welcome", "got it", "okay", "fine"
]

def is_allowed_word(text: str) -> bool:
    return text.lower().strip() in ALLOWED_WORDS

# =========================
# HANDLERS
# =========================

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = update.chat_member
    if not chat_member:
        return
    if chat_member.new_chat_member.status == ChatMemberStatus.MEMBER and \
       chat_member.old_chat_member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, None]:
        user = chat_member.new_chat_member.user
        user_id = user.id
        username = user.username or user.first_name
        add_user(user_id, username, user.first_name)
        try:
            until_date = datetime.fromtimestamp(time.time() + (RESTRICTION_HOURS * 3600))
            await context.bot.restrict_chat_member(
                chat_id=chat_member.chat.id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_date
            )
            logger.info(f"🔇 Restricted {username}({user_id}) for {RESTRICTION_HOURS}h")
        except Exception as e:
            logger.error(f"Failed to restrict: {e}")
        welcome_text = (
            f"🔒 **WELCOME TO ESCROW SPARTANS!** 🔒\n\n"
            f"Hello @{username}!\n\n"
            f"⏰ You are restricted for {RESTRICTION_HOURS} hours.\n"
            f"📵 You cannot send messages until restriction ends.\n\n"
            f"👇 **Type /start to complete verification**\n\n"
            f"💰 Admin: {ADMIN_USERNAME}"
        )
        await context.bot.send_message(chat_id=chat_member.chat.id, text=welcome_text, parse_mode='Markdown')

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = msg.from_user
    user_id = user.id
    username = user.username or user.first_name
    add_user(user_id, username, user.first_name)
    verify_user(user_id)
    await msg.delete()
    reply_text = (
        f"✅ **Verification Complete!**\n\n"
        f"👤 Username: @{username}\n"
        f"🆔 User ID: `{user_id}`\n\n"
        f"✅ Thanks for verifying! Your ID has been saved.\n"
        f"⏰ Restriction will auto-remove after {RESTRICTION_HOURS} hours.\n\n"
        f"💰 Admin: {ADMIN_USERNAME}"
    )
    await msg.reply_text(reply_text, parse_mode='Markdown')

async def filter_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    chat = msg.chat
    user = msg.from_user
    user_id = user.id
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
    if user_id == ADMIN_ID:
        return
    if not can_send_message(user_id):
        await msg.delete()
        remaining = get_restriction_time_left(user_id)
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        await msg.reply_text(
            f"🔴 **You are restricted!** 🔴\n\n"
            f"⏰ Remaining: {hours}h {minutes}m\n\n"
            f"✅ Type /start to verify your ID\n\n"
            f"⚠️ Restriction will auto-remove after {RESTRICTION_HOURS} hours."
        )
        return
    text = msg.text.strip()
    if text.startswith('/'):
        return
    if is_allowed_word(text):
        return
    if text.lower().startswith(('#buying', '#selling')):
        if is_valid_trade_post(text):
            return
        else:
            await msg.delete()
            await msg.reply_text(
                "❌ **Invalid Trade Post Format!** ❌\n\n"
                "📝 **Use this exact format:**\n"
                "```\n"
                "#buying or #selling\n\n"
                "Chain: BEP20\n"
                "Amount[USDT]: 100\n"
                "Amount[INR]: 8500\n"
                "Rate[INR/USDT]: 85\n"
                "Payment method: Bank/UPI\n"
                "```\n\n"
                "✅ **OR use allowed words like:** hi, hello, dm, done, paid, ok, yes, available"
            )
            return
    await msg.delete()
    await msg.reply_text(
        "❌ **Message Deleted - Not Allowed!** ❌\n\n"
        "📝 **Only these are allowed:**\n"
        "• Trade posts (#buying / #selling format)\n"
        "• Commands: /start, /escrow, /help\n"
        "• Simple words: hi, hello, dm, done, paid, ok, yes, available\n\n"
        "✅ Please use allowed format only."
    )

async def username_change_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return
    user = msg.from_user
    user_id = user.id
    new_username = user.username or user.first_name
    old_username = update_username_in_db(user_id, new_username)
    if old_username and old_username != new_username:
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
                chat_id=main_group_id,
                text=f"🔄 **Username Change Alert**\n\n🆔 ID: `{user_id}`\n📛 Old: @{old_username}\n✨ New: @{new_username}",
                parse_mode='Markdown'
            )

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
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
        await msg.reply_text("❌ Please set a Telegram username first!")
        return
    buyer_username = context.args[0].replace("@", "").lower()
    if seller_username == buyer_username:
        await msg.reply_text("❌ Cannot escrow with yourself!")
        return
    room = get_free_room()
    if not room:
        await msg.reply_text("❌ All escrow rooms are busy! Please wait.")
        return
    add_active_escrow(room["room_id"], room["room_num"], seller_username, seller.id, buyer_username, 0, room["invite_link"])
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👨‍💼 I AM SELLER", callback_data=f"role_seller_{room['room_id']}"),
         InlineKeyboardButton("👨‍💻 I AM BUYER", callback_data=f"role_buyer_{room['room_id']}")]
    ])
    await context.bot.send_message(
        chat_id=room["room_id"],
        text=f"🔒 **ESCROW ROOM #{room['room_num']}**\n\n👇 **Please select your position:**",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await msg.reply_text(
        f"🔒 **ESCROW ROOM #{room['room_num']}**\n\n"
        f"👤 Seller: @{seller_username}\n👤 Buyer: @{buyer_username}\n\n"
        f"🔗 {room['invite_link']}\n\n💰 Admin Wallet: `{ADMIN_WALLET}`",
        parse_mode='Markdown'
    )

async def role_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    room_id = int(data.split("_")[2])
    role = data.split("_")[1]
    escrow = get_active_escrow(room_id)
    if not escrow:
        await query.edit_message_text("❌ This escrow room is no longer active.")
        return
    if role == "seller":
        if escrow["seller_id"] != user.id:
            await query.edit_message_text("❌ You are not the seller for this escrow!")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ CONFIRM PAID", callback_data=f"confirm_paid_{room_id}")]
        ])
        await query.edit_message_text(
            f"✅ You are registered as **SELLER**\n\n"
            f"💰 **ADMIN WALLET (BEP20):**\n`{ADMIN_WALLET}`\n\n"
            f"📌 Send USDT to this address\n"
            f"📌 After sending, click **CONFIRM PAID**\n\n"
            f"⚠️ Do not proceed until admin confirms!",
            parse_mode='Markdown',
            reply_markup=keyboard
        )
    elif role == "buyer":
        if escrow["buyer_id"] != 0 and escrow["buyer_id"] != user.id:
            await query.edit_message_text("❌ Buyer already assigned!")
            return
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE active_escrows SET buyer_id=? WHERE room_id=?", (user.id, room_id))
        conn.commit()
        conn.close()
        await query.edit_message_text(
            f"✅ You are registered as **BUYER**\n\n"
            f"📌 **WAIT FOR INSTRUCTIONS**\n\n"
            f"1️⃣ Seller will send USDT to admin\n"
            f"2️⃣ Admin will verify and say **PROCEED**\n"
            f"3️⃣ THEN you can pay INR to seller\n"
            f"4️⃣ Type `/paid` and share screenshot in THIS ROOM\n\n"
            f"⚠️ Do NOT pay INR until admin says PROCEED!",
            parse_mode='Markdown'
        )

async def confirm_paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room_id = int(query.data.split("_")[2])
    escrow = get_active_escrow(room_id)
    if not escrow:
        await query.edit_message_text("❌ Escrow not found!")
        return
    update_seller_confirmed(room_id)
    await query.edit_message_text(
        f"💰 @{escrow['seller_username']} has confirmed USDT sent to admin wallet.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ **WAITING FOR ADMIN CONFIRMATION...**\n\n"
        f"❌ **BUYER: DO NOT PAY INR YET!**\n"
        f"❌ Wait for admin to type `/proceed`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Admin will verify USDT receipt and respond shortly.",
        parse_mode='Markdown'
    )
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"💰 **USDT CONFIRMATION**\n\nSeller: @{escrow['seller_username']} (ID: `{escrow['seller_id']}`)\nBuyer: @{escrow['buyer_username']} (ID: `{escrow['buyer_id']}`)\nRoom: #{escrow['room_num']}\n\n✅ Seller confirmed USDT sent!\n📌 Type `/proceed` in the room after verifying.",
        parse_mode='Markdown'
    )

async def proceed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    room_id = msg.chat_id
    escrow = get_active_escrow(room_id)
    if not escrow:
        await msg.reply_text("❌ No active escrow in this room!")
        return
    update_admin_proceeded(room_id)
    await msg.reply_text(
        f"✅ **ADMIN VERIFIED:** USDT received in admin wallet!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 @{escrow['buyer_username']}, you can NOW pay INR to @{escrow['seller_username']}\n\n"
        f"1️⃣ Send payment to seller's bank/UPI\n"
        f"2️⃣ Type `/paid` and share payment screenshot in THIS ROOM\n"
        f"3️⃣ Admin will verify and complete deal\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ After payment, type: `/paid`",
        parse_mode='Markdown'
    )

async def paid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    room_id = msg.chat_id
    user = msg.from_user
    escrow = get_active_escrow(room_id)
    if not escrow:
        await msg.reply_text("❌ No active escrow in this room!")
        return
    if escrow["buyer_id"] != user.id:
        await msg.reply_text("❌ Only buyer can use /paid!")
        return
    if not escrow["admin_proceeded"]:
        await msg.reply_text("❌ Admin has not proceeded yet! Wait for `/proceed` from admin.")
        return
    await msg.reply_text(
        f"📸 @{user.username} has sent payment.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ **WAITING FOR ADMIN TO VERIFY PAYMENT...**\n\n"
        f"Admin will check and type `/complete`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode='Markdown'
    )
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"💳 **PAYMENT CONFIRMATION NEEDED**\n\nBuyer: @{escrow['buyer_username']} (ID: `{escrow['buyer_id']}`)\nSeller: @{escrow['seller_username']} (ID: `{escrow['seller_id']}`)\nRoom: #{escrow['room_num']}\n\n✅ Buyer claims payment sent to seller!\n📌 Verify and type `/complete` in the room.",
        parse_mode='Markdown'
    )

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    room_id = msg.chat_id
    escrow = get_active_escrow(room_id)
    if not escrow:
        await msg.reply_text("❌ No active escrow in this room!")
        return
    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60
    remove_active_escrow(room_id, "completed", end_time, duration)
    free_room(room_id)
    await msg.reply_text(
        f"✅✅ **DEAL COMPLETED SUCCESSFULLY!** ✅✅\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **DEAL SUMMARY:**\n\n"
        f"👤 Seller: @{escrow['seller_username']} (ID: `{escrow['seller_id']}`)\n"
        f"👤 Buyer: @{escrow['buyer_username']} (ID: `{escrow['buyer_id']}`)\n"
        f"🆔 Room: #{escrow['room_num']}\n"
        f"⏱ Duration: {minutes}m {seconds}s\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 USDT has been released to @{escrow['buyer_username']}\n"
        f"✅ Deal completed by Admin\n\n"
        f"🔓 Room is now free for new escrow.",
        parse_mode='Markdown'
    )
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
            chat_id=main_group_id,
            text=f"✅ **DEAL COMPLETED** in Escrow Room #{escrow['room_num']}!\n\n"
                 f"👤 Seller: @{escrow['seller_username']} (ID: `{escrow['seller_id']}`)\n"
                 f"👤 Buyer: @{escrow['buyer_username']} (ID: `{escrow['buyer_id']}`)\n"
                 f"⏱ Duration: {minutes}m {seconds}s\n\n"
                 f"✅ Completed by Admin {ADMIN_USERNAME}",
            parse_mode='Markdown'
        )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    room_id = msg.chat_id
    escrow = get_active_escrow(room_id)
    if not escrow:
        await msg.reply_text("❌ No active escrow in this room!")
        return
    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60
    remove_active_escrow(room_id, "cancelled", end_time, duration)
    free_room(room_id)
    await msg.reply_text(
        f"❌ **DEAL CANCELLED BY ADMIN!**\n\n"
        f"👤 Seller: @{escrow['seller_username']} (ID: `{escrow['seller_id']}`)\n"
        f"👤 Buyer: @{escrow['buyer_username']} (ID: `{escrow['buyer_id']}`)\n"
        f"⏱ Duration: {minutes}m {seconds}s\n\n"
        f"🔓 Room is now free for new escrow.",
        parse_mode='Markdown'
    )
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
            chat_id=main_group_id,
            text=f"❌ **DEAL CANCELLED** in Escrow Room #{escrow['room_num']}!\n\n"
                 f"👤 Seller: @{escrow['seller_username']}\n"
                 f"👤 Buyer: @{escrow['buyer_username']}\n\n"
                 f"❌ Cancelled by Admin {ADMIN_USERNAME}"
        )

async def show_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user.id == ADMIN_ID:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT room_num, seller_username, buyer_username FROM active_escrows")
        results = c.fetchall()
        conn.close()
        if results:
            text = "📋 **Active Escrows:**\n"
            for r in results:
                text += f"Room {r[0]}: @{r[1]} → @{r[2]}\n"
            await update.message.reply_text(text, parse_mode='Markdown')
        else:
            await update.message.reply_text("No active escrows.")

async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user.id == ADMIN_ID:
        await update.message.reply_text(f"🆔 Chat ID: `{update.message.chat_id}`", parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 **Help Guide**\n\n"
        "🔹 `/start` - Verify your ID\n"
        "🔹 `/escrow @username` - Start a new escrow deal\n"
        "🔹 `/paid` - (In escrow room) Share payment proof\n"
        "🔹 `/help` - Show this help message\n\n"
        "*Admin Commands:*\n"
        "🔸 `/proceed` - Allow buyer to pay (after USDT received)\n"
        "🔸 `/complete` - Complete deal (after buyer paid)\n"
        "🔸 `/cancel` - Cancel current deal\n"
        "🔸 `/show` - Show active escrows\n"
        "🔸 `/getid` - Get current chat ID\n\n"
        f"💰 Admin Wallet: `{ADMIN_WALLET}`",
        parse_mode='Markdown'
    )

# =========================
# AUTO UNRESTRICT TASK (FIXED)
# =========================
async def auto_unrestrict(app):
    while True:
        await asyncio.sleep(3600)  # Check every hour
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, restriction_end_time FROM users WHERE verified=1 AND restriction_end_time > 0")
        users = c.fetchall()
        conn.close()
        
        main_group_id = None
        try:
            if str(GROUP_ID_INPUT).lstrip('-').isdigit():
                main_group_id = int(GROUP_ID_INPUT)
            else:
                chat = await app.bot.get_chat(GROUP_ID_INPUT)
                main_group_id = chat.id
        except:
            pass
        
        if not main_group_id:
            continue
            
        for user_id, restriction_end in users:
            if time.time() >= restriction_end:
                try:
                    await app.bot.restrict_chat_member(
                        chat_id=main_group_id,
                        user_id=user_id,
                        permissions=ChatPermissions(
                            can_send_messages=True,
                            can_send_media_messages=True,
                            can_send_other_messages=True,
                            can_add_web_page_previews=True
                        )
                    )
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute("UPDATE users SET restriction_end_time=0 WHERE user_id=?", (user_id,))
                    conn.commit()
                    conn.close()
                    
                    try:
                        user_info = await app.bot.get_chat(user_id)
                        username = user_info.username or user_info.first_name
                        await app.bot.send_message(
                            chat_id=main_group_id,
                            text=f"✅ @{username} restriction removed! You can now send messages."
                        )
                    except:
                        pass
                    logger.info(f"Auto-unrestricted user {user_id}")
                except Exception as e:
                    logger.error(f"Auto-unrestrict failed for {user_id}: {e}")

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
    
    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("proceed", proceed_command))
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("paid", paid_command))
    app.add_handler(CommandHandler("show", show_command))
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Message filters
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_messages))
    
    # Username change check
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, username_change_check))
    
    # New member handler
    app.add_handler(ChatMemberHandler(handle_new_member, ChatMemberHandler.CHAT_MEMBER))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(role_callback, pattern="^role_"))
    app.add_handler(CallbackQueryHandler(confirm_paid_callback, pattern="^confirm_paid_"))
    
    logger.info("Bot started...")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Start auto unrestrict task
    asyncio.create_task(auto_unrestrict(app))
    
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
