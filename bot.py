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
ADMIN_ID = int(os.getenv("ADMIN_ID", "6722137021"))
ADMIN_USERNAME = "@crypto_8099"
ADMIN_WALLET = os.getenv("ADMIN_WALLET", "0xYourAdminWalletAddressHere")  # Add this in .env
PORT = int(os.getenv("PORT", 10000))

GROUP_ID = GROUP_ID_INPUT

# =========================
# ESCROW ROOMS (Pre-created)
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
        "room_id": -1004123456789,  # Replace with your actual Room 3 ID
        "invite_link": "https://t.me/+YourRoom3Link",  # Replace with actual invite link
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

    conn.commit()
    conn.close()
    logger.info("Database initialized")

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

def add_active_escrow(
    seller_username,
    buyer_username,
    seller_id,
    buyer_id,
    room_id,
    room_num,
    start_time,
    invite_link
):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        INSERT INTO active_escrows
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        seller_username,
        buyer_username,
        seller_id,
        buyer_id,
        room_id,
        room_num,
        start_time,
        invite_link
    ))

    conn.commit()
    conn.close()
    logger.info(f"✅ Active escrow added: Room {room_num} | Seller: {seller_username}({seller_id}) | Buyer: {buyer_username}({buyer_id})")

def get_active_escrow_by_room(room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        SELECT * FROM active_escrows
        WHERE room_id=?
    """, (room_id,))

    result = c.fetchone()
    conn.close()

    if not result:
        logger.warning(f"No active escrow found for room_id: {room_id}")
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

    c.execute("""
        SELECT * FROM active_escrows
        WHERE room_id=?
    """, (room_id,))

    escrow = c.fetchone()

    if escrow:
        c.execute("""
            INSERT INTO escrow_history
            (
                seller_username,
                buyer_username,
                seller_id,
                buyer_id,
                room_num,
                start_time,
                end_time,
                status,
                duration_seconds,
                completed_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            escrow[0],
            escrow[1],
            escrow[2],
            escrow[3],
            escrow[5],
            escrow[6],
            end_time,
            status,
            duration,
            completed_by
        ))

        c.execute("""
            DELETE FROM active_escrows
            WHERE room_id=?
        """, (room_id,))
        
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

ALLOWED_MESSAGES = [
    "dm", "hi", "hello", "check dm", "done", 
    "paid", "sent", "ok", "yes", "available"
]

# =========================
# GET ID COMMAND (ONLY ADMIN)
# =========================

async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg:
        return
    
    user = msg.from_user
    
    if user.id != ADMIN_ID:
        await msg.reply_text("❌ Access Denied! Only admin can use this command.")
        return
    
    await msg.reply_text(f"🆔 Chat ID: `{msg.chat_id}`\n🆔 Your ID: `{user.id}`", parse_mode='Markdown')
    logger.info(f"Admin used /getid in chat {msg.chat_id}")

# =========================
# SHOW ESCROWS COMMAND (DEBUG - ONLY ADMIN)
# =========================

async def show_escrows_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT room_id, room_num, seller_username, buyer_username, seller_id, buyer_id, start_time FROM active_escrows")
    results = c.fetchall()
    conn.close()
    
    if results:
        text = "📋 **Active Escrows:**\n\n"
        for r in results:
            text += f"Room #{r[1]}: @{r[2]} (ID: `{r[4]}`) → @{r[3]} (ID: `{r[5]}`)\nRoom ID: `{r[0]}`\n\n"
        await msg.reply_text(text, parse_mode='Markdown')
    else:
        await msg.reply_text("📋 No active escrows found.")

# =========================
# ESCROW COMMAND (MODIFIED WITH IDs + NEW PROCESS)
# =========================

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg:
        return

    # Check if command is used in MAIN GROUP
    chat_id = msg.chat_id
    main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
    
    if chat_id != main_group_id:
        await msg.reply_text("❌ /escrow command can only be used in the main group!")
        return

    if len(context.args) < 1:
        await msg.reply_text("❌ Usage:\n\n/escrow @username")
        return

    seller = msg.from_user

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

    # Get buyer ID
    buyer_id = 0
    try:
        buyer_chat = await context.bot.get_chat(f"@{buyer_username}")
        buyer_id = buyer_chat.id
        logger.info(f"Found buyer ID: {buyer_id} for @{buyer_username}")
    except Exception as e:
        logger.error(f"Could not fetch buyer ID for @{buyer_username}: {e}")

    start_time = time.time()

    # Get permanent invite link
    invite_link = room.get("invite_link")
    if not invite_link:
        try:
            link_obj = await context.bot.create_chat_invite_link(
                chat_id=room_id,
                member_limit=2,
                expire_date=0
            )
            invite_link = link_obj.invite_link
            room["invite_link"] = invite_link
            logger.info(f"Created new invite link for room {room['room_num']}")
        except Exception as e:
            logger.error(f"Failed to create invite link: {e}")
            try:
                invite_link = await context.bot.export_chat_invite_link(chat_id=room_id)
                room["invite_link"] = invite_link
            except:
                invite_link = room["invite_link"]

    # Add to database
    try:
        add_active_escrow(
            seller_username,
            buyer_username,
            seller_id,
            buyer_id,
            room["room_id"],
            room["room_num"],
            start_time,
            invite_link
        )
        
        # Verify it was added
        verify = get_active_escrow_by_room(room["room_id"])
        if verify:
            logger.info(f"✅ VERIFIED: Escrow exists in database for room {room['room_num']}")
        else:
            logger.error(f"❌ FAILED: Escrow not found in database after add!")
            await msg.reply_text("❌ Database error! Please try again.")
            free_room(room_id)
            return
            
    except Exception as e:
        logger.error(f"❌ Database insert error: {e}")
        await msg.reply_text(f"❌ Database error: {e}")
        free_room(room_id)
        return

    # SEND MESSAGE IN ESCROW ROOM (WITH USER IDs + NEW PROCESS)
    try:
        await context.bot.send_message(
            chat_id=room["room_id"],
            text=(
                f"🔒 **ESCROW STARTED**\n\n"
                f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n"
                f"👤 Buyer: @{buyer_username} (ID: `{buyer_id}`)\n"
                f"🆔 Room #{room['room_num']}\n\n"
                f"⚠️ **IMPORTANT RULES:**\n"
                f"• ONLY ADMIN {ADMIN_USERNAME} can complete or cancel\n"
                f"• Seller CANNOT cancel\n"
                f"• Buyer CANNOT cancel\n\n"
                f"📝 **ESCROW PROCESS (3 STEPS):**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
              f"**STEP 1️⃣** - Seller sends **USDT amount to Admin**\n"
                f"          for safekeeping during escrow\n\n"
                f"**STEP 2️⃣** - Buyer sends payment amount to Seller\n"
                f"          (Bank transfer / UPI / as agreed)\n\n"
                f"**STEP 3️⃣** - After seller confirms payment received,\n"
                f"          Admin releases USDT to Buyer\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚠️ For disputes, contact admin directly.\n"
                f"💰 **Admin Wallet (BEP20):** `{ADMIN_WALLET}`"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Room message error: {e}")

    # MAIN GROUP MESSAGE WITH LINK AND IDs
    await msg.reply_text(
        f"🔒 **ESCROW ROOM #{room['room_num']}**\n\n"
        f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n"
        f"👤 Buyer: @{buyer_username} (ID: `{buyer_id}`)\n\n"
        f"🔗 **Join Link:**\n{invite_link}\n\n"
        f"⚠️ **Beware of Scammers** ⚠️\n\n"
        f"📝 **Escrow Rules & Process:**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• ONLY ADMIN {ADMIN_USERNAME} can complete/cancel\n"
        f"\n"
        f"**STEP 1:** Buyer → Payment to Seller (INR)\n"
        f"**STEP 2:** Seller → USDT to Admin (for holding)\n"
        f"**STEP 3:** Admin → Release USDT to Buyer\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Admin will resolve all disputes\n\n"
        f"🆔 **Save these IDs for reference**\n"
        f"💰 Admin Wallet: `{ADMIN_WALLET}`"
    )

    # Send backup link to seller DM with admin wallet
    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                f"✅ **Escrow Room #{room['room_num']} Created!**\n\n"
                f"👤 Buyer: @{buyer_username} (ID: `{buyer_id}`)\n\n"
                f"🔗 **Join Link:**\n{invite_link}\n\n"
                f"⚠️ Share this link with buyer!\n\n"
                f"📝 **How this Escrow Works:**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"**1️⃣** Buyer sends INR payment to your bank/UPI\n"
                f"**2️⃣** You send **USDT amount to Admin wallet** below\n"
                f"**3️⃣** After you confirm payment received,\n"
                f"       Admin releases USDT to Buyer\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 **Admin USDT Wallet (BEP20):**\n"
                f"`{ADMIN_WALLET}`\n\n"
                f"⚠️ **Send ONLY BEP20 USDT to this address!**\n"
                f"⚠️ **You cannot cancel this deal - only admin can!**\n\n"
                f"📌 After sending USDT to admin, type:\n"
                f"`/confirm_paid` in the escrow room"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Seller DM error: {e}")
    
    # Send to buyer DM as well
    if buyer_id:
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"✅ **You have been added to Escrow Room #{room['room_num']}**\n\n"
                    f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n\n"
                    f"🔗 **Join Link:**\n{invite_link}\n\n"
                    f"📝 **Escrow Process for Buyer:**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"**1️⃣** Send INR payment to Seller as agreed\n"
                    f"**2️⃣** Seller will send USDT to Admin for holding\n"
                    f"**3️⃣** Admin will release USDT to you after\n"
                    f"       seller confirms payment received\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"⚠️ Only admin can complete/cancel this deal!\n"
                    f"💰 Admin holds USDT until payment confirmation"
                ),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Buyer DM error: {e}")

    logger.info(f"✅ Escrow created: {seller_username}({seller_id}) -> {buyer_username}({buyer_id}) in room #{room['room_num']}")

# =========================
# CONFIRM PAID COMMAND (Seller confirms they sent USDT to admin)
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
    
    # Only seller can confirm
    if user.id != escrow['seller_id']:
        await msg.reply_text("❌ Only seller can use /confirm_paid after sending USDT to admin!")
        return
    
    # Notify admin
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"💰 **USDT PAYMENT CONFIRMATION**\n\n"
                f"Seller: @{escrow['seller_username']} (ID: `{escrow['seller_id']}`)\n"
                f"Buyer: @{escrow['buyer_username']} (ID: `{escrow['buyer_id']}`)\n"
                f"Room: #{escrow['room_num']}\n\n"
                f"✅ Seller claims they have sent USDT to admin wallet.\n\n"
                f"📌 **Verify and then use:**\n"
                f"`/complete` in Room ID: `{room_id}`\n\n"
                f"⚠️ Only complete after verifying USDT receipt!"
            ),
            parse_mode='Markdown'
        )
        
        await msg.reply_text(
            f"✅ **Confirmation sent to admin!**\n\n"
            f"Admin will verify the USDT and complete the deal.\n"
            f"Please wait for admin to release funds to buyer."
        )
        
        # Also notify in escrow room
        await context.bot.send_message(
            chat_id=room_id,
            text=(
                f"💰 @{escrow['seller_username']} has confirmed sending USDT to admin.\n"
                f"📌 Admin will verify and complete the deal shortly."
            )
        )
        
    except Exception as e:
        logger.error(f"Confirm paid error: {e}")
        await msg.reply_text("❌ Failed to notify admin. Contact admin directly.")

# =========================
# COMPLETE COMMAND (MODIFIED - WITH ID DISPLAY)
# =========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg:
        return

    room_id = msg.chat_id
    user = msg.from_user

    logger.info(f"🔍 COMPLETE: Room={room_id}, User={user.id}, Username=@{user.username}")

    escrow = get_active_escrow_by_room(room_id)

    if not escrow:
        await msg.reply_text(
            "❌ **No active escrow in this room!**\n\n"
            "Possible reasons:\n"
            "1. /escrow command was not used in MAIN GROUP\n"
            "2. Escrow was already completed/cancelled\n"
            "3. Database error occurred\n\n"
            "Please create a new escrow using /escrow @username in the main group."
        )
        return
    
    # ONLY ADMIN can complete
    if user.id != ADMIN_ID:
        await msg.reply_text(
            f"❌ **Only Admin {ADMIN_USERNAME} can complete this deal!**\n\n"
            f"Seller: @{escrow['seller_username']} (ID: `{escrow['seller_id']}`)\n"
            f"Buyer: @{escrow['buyer_username']} (ID: `{escrow['buyer_id']}`)\n\n"
            f"Cannot use /complete."
        )
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    seller_username = escrow['seller_username']
    buyer_username = escrow['buyer_username']
    seller_id = escrow['seller_id']
    buyer_id = escrow['buyer_id']
    room_num = escrow['room_num']

    remove_escrow(room_id, "completed", end_time, duration, completed_by="Admin")
    free_room(room_id)

    await msg.reply_text(
        f"✅ **DEAL COMPLETED BY ADMIN!**\n\n"
        f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n"
        f"👤 Buyer: @{buyer_username} (ID: `{buyer_id}`)\n"
        f"⏱ Duration: {minutes}m {seconds}s\n\n"
        f"🟢 Room is now available for new escrow."
    )

    # Notify in main group with IDs and process summary
    try:
        main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
        await context.bot.send_message(
            chat_id=main_group_id,
            text=(
                f"✅ **DEAL COMPLETED** in Escrow Room #{room_num}!\n\n"
                f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n"
                f"👤 Buyer: @{buyer_username} (ID: `{buyer_id}`)\n"
                f"⏱ Duration: {minutes}m {seconds}s\n\n"
                f"✅ Completed by Admin {ADMIN_USERNAME}\n\n"
                f"📝 **Process Followed:**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"1️⃣ Buyer → Payment to Seller ✅\n"
                f"2️⃣ Seller → USDT to Admin ✅\n"
                f"3️⃣ Admin → USDT released to Buyer ✅\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to send completion notice: {e}")
    
    # Notify buyer
    if buyer_id:
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"✅ **Deal Completed!**\n\n"
                    f"Admin has released USDT to you.\n"
                    f"Check your wallet.\n\n"
                    f"Seller: @{seller_username}\n"
                    f"Room #{room_num}\n"
                    f"Duration: {minutes}m {seconds}s"
                )
            )
        except Exception as e:
            logger.error(f"Buyer notification error: {e}")

    logger.info(f"✅ Escrow completed by Admin: {seller_username}({seller_id}) -> {buyer_username}({buyer_id})")

# =========================
# CANCEL COMMAND (ONLY ADMIN)
# =========================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg:
        return

    room_id = msg.chat_id
    user = msg.from_user

    logger.info(f"🔍 CANCEL: Room={room_id}, User={user.id}, Username=@{user.username}")

    escrow = get_active_escrow_by_room(room_id)

    if not escrow:
        await msg.reply_text(
            "❌ **No active escrow in this room!**\n\n"
            "Please create a new escrow using /escrow @username in the main group."
        )
        return
    
    # ONLY ADMIN can cancel
    if user.id != ADMIN_ID:
        await msg.reply_text(
            f"❌ **Only Admin {ADMIN_USERNAME} can cancel this deal!**\n\n"
            f"Seller (@{escrow['seller_username']}) and Buyer (@{escrow['buyer_username']}) cannot cancel."
        )
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    seller_username = escrow['seller_username']
    buyer_username = escrow['buyer_username']
    seller_id = escrow['seller_id']
    buyer_id = escrow['buyer_id']
    room_num = escrow['room_num']

    remove_escrow(room_id, "cancelled", end_time, duration, completed_by="Admin")
    free_room(room_id)

    await msg.reply_text(
        f"❌ **DEAL CANCELLED BY ADMIN!**\n\n"
        f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n"
        f"👤 Buyer: @{buyer_username} (ID: `{buyer_id}`)\n"
        f"⏱ Duration: {minutes}m {seconds}s\n\n"
        f"🟢 Room is now available for new escrow.\n\n"
        f"💰 **Important:** Admin will return USDT to seller if already sent."
    )

    # Notify in main group
    try:
        main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
        await context.bot.send_message(
            chat_id=main_group_id,
            text=f"❌ **DEAL CANCELLED BY ADMIN** in Escrow Room #{room_num}!\n\n"
                 f"👤 Seller: @{seller_username} (ID: `{seller_id}`)\n"
                 f"👤 Buyer: @{buyer_username} (ID: `{buyer_id}`)\n"
                 f"⏱ Duration: {minutes}m {seconds}s\n\n"
                 f"❌ Cancelled by Admin {ADMIN_USERNAME}"
        )
    except Exception as e:
        logger.error(f"Failed to send cancellation notice: {e}")

    logger.info(f"❌ Escrow cancelled by Admin: {seller_username}({seller_id}) -> {buyer_username}({buyer_id})")

# =========================
# FILTER MESSAGES
# =========================

async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg or not msg.text:
        return

    chat = msg.chat
    chat_username = (chat.username or "").lower()
    target_group = str(GROUP_ID_INPUT).replace("@", "").lower()

    if (chat_username != target_group and str(chat.id) != str(GROUP_ID)):
        return

    try:
        member = await context.bot.get_chat_member(
            msg.chat_id,
            msg.from_user.id
        )
        if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return
    except Exception as e:
        logger.error(e)

    text_lower = msg.text.strip().lower()

    if text_lower in ALLOWED_MESSAGES:
        return

    if not is_valid(msg.text):
        try:
            await msg.delete()
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=(
                    "❌ Invalid Post Format!\n\n"
                    "Use this format:\n\n"
                    "#buying or #selling\n\n"
                    "Chain: BEP20\n"
                    "Amount[USDT]: ?\n"
                    "Amount[INR]: ?\n"
                    "Rate[INR/USDT]: ?\n"
                    "Payment method: ?\n\n"
                    "escrow fee:[buyer Or seller)? \n"
                )
            )
        except Exception as e:
            logger.error(e)

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

    # Commands
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("confirm_paid", confirm_paid_command))  # NEW COMMAND
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("show", show_escrows_command))  # Debug command
    
    # Message filter
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            filter_msg
        )
    )

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
    logger.info("Starting services...")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    flask_app.run(host="0.0.0.0", port=PORT)
