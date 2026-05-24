import re
import os
import time
import sqlite3
import logging
import threading
import asyncio
from typing import Dict

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

GROUP_ID = GROUP_ID_INPUT

# Store active rooms in memory
ACTIVE_ROOMS: Dict[int, dict] = {}

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
                room_num,
                start_time,
                end_time,
                status,
                duration_seconds,
                completed_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            escrow[0],
            escrow[1],
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

    conn.commit()
    conn.close()

def get_next_room_num():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT MAX(room_num) FROM active_escrows")
    result = c.fetchone()
    conn.close()
    
    if result and result[0]:
        return result[0] + 1
    return 1

# =========================
# CREATE NEW ESCROW ROOM
# =========================

async def create_escrow_room(context: ContextTypes.DEFAULT_TYPE, 
                              seller_username: str, 
                              buyer_username: str,
                              seller_id: int,
                              buyer_id: int) -> dict:
    """Create a new private escrow room dynamically"""
    
    room_num = get_next_room_num()
    room_title = f"🔒 Escrow #{room_num} | @{seller_username} x @{buyer_username}"
    
    try:
        # Create new supergroup
        new_group = await context.bot.create_supergroup(
            title=room_title,
            description=f"Private Escrow Room\nSeller: @{seller_username}\nBuyer: @{buyer_username}"
        )
        
        room_id = new_group.id
        
        # Make it private
        try:
            await context.bot.set_chat_username(
                chat_id=room_id,
                username=None
            )
        except:
            pass
        
        # Create invite link (1 hour expiry)
        invite_link_obj = await context.bot.create_chat_invite_link(
            chat_id=room_id,
            member_limit=2,
            expire_date=int(time.time()) + 3600,
            creates_join_request=True
        )
        
        invite_link = invite_link_obj.invite_link
        
        room_info = {
            "room_id": room_id,
            "room_num": room_num,
            "invite_link": invite_link,
            "seller_username": seller_username,
            "buyer_username": buyer_username,
            "seller_id": seller_id,
            "buyer_id": buyer_id,
            "success": True
        }
        
        return room_info
        
    except Exception as e:
        logger.error(f"Failed to create escrow room: {e}")
        return {"success": False, "error": str(e)}

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
    
    await msg.reply_text(f"🆔 Chat ID: `{msg.chat_id}`", parse_mode='Markdown')

# =========================
# ESCROW COMMAND - CREATES NEW ROOM
# =========================

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg:
        return

    if len(context.args) < 1:
        await msg.reply_text("❌ Usage:\n\n/escrow @username")
        return

    seller = msg.from_user

    if not seller.username:
        await msg.reply_text("❌ Please set Telegram username first!")
        return

    seller_username = seller.username.lower()
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

    # Get buyer ID
    buyer_id = 0
    try:
        buyer_chat = await context.bot.get_chat(f"@{buyer_username}")
        buyer_id = buyer_chat.id
    except Exception as e:
        logger.error(f"Could not fetch buyer ID: {e}")

    # CREATE NEW ROOM
    await msg.reply_text("🔄 Creating new escrow room... Please wait.")
    
    room_info = await create_escrow_room(
        context, 
        seller_username, 
        buyer_username, 
        seller.id, 
        buyer_id
    )
    
    if not room_info.get("success"):
        await msg.reply_text(f"❌ Failed to create room: {room_info.get('error', 'Unknown error')}")
        return

    start_time = time.time()

    add_active_escrow(
        seller_username,
        buyer_username,
        seller.id,
        buyer_id,
        room_info["room_id"],
        room_info["room_num"],
        start_time,
        room_info["invite_link"]
    )

    # SEND MESSAGE IN ESCROW ROOM
    try:
        await context.bot.send_message(
            chat_id=room_info["room_id"],
            text=(
                f"🔒 **PRIVATE ESCROW STARTED**\n\n"
                f"👤 Seller: @{seller_username}\n"
                f"👤 Buyer: @{buyer_username}\n"
                f"🆔 Room #{room_info['room_num']}\n\n"
                f"⚠️ **PERMISSIONS:**\n"
                f"• ONLY ADMIN can use /complete\n"
                f"• Seller & Buyer can use /cancel\n\n"
                f"📝 **Commands:**\n"
                f"/cancel - Cancel deal (Seller/Buyer)\n"
                f"⚠️ Only Admin can complete the deal!\n\n"
                f"🔗 This link is valid for 1 hour only."
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Room message error: {e}")

    # MAIN GROUP MESSAGE WITH LINK
    await msg.reply_text(
        f"🔒 **NEW ESCROW ROOM CREATED!**\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n"
        f"🆔 Room #{room_info['room_num']}\n\n"
        f"🔗 **Join Link:**\n{room_info['invite_link']}\n\n"
        f"⚠️ **Beware of Scammers** ⚠️\n\n"
        f"📝 **Important Rules:**\n"
        f"• ONLY ADMIN (@crypto_8099) can complete the deal\n"
        f"• Seller & Buyer can cancel the deal\n"
        f"• Both parties must agree before admin completes\n\n"
        f"🔗 **Link expires in 1 hour!**\n"
        f"⚠️ **This is a new private room created just for this escrow.**",
        parse_mode='Markdown'
    )

    # Send backup link to seller DM
    try:
        await context.bot.send_message(
            chat_id=seller.id,
            text=(
                f"✅ **Escrow Room #{room_info['room_num']} Created!**\n\n"
                f"👤 Buyer: @{buyer_username}\n\n"
                f"🔗 **Join Link:**\n{room_info['invite_link']}\n\n"
                f"⚠️ **Share this link with buyer!**\n"
                f"⚠️ Link expires in 1 hour!\n\n"
                f"📝 **Commands (use in escrow room):**\n"
                f"/cancel - Cancel the deal"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Seller DM error: {e}")

    logger.info(f"Escrow created: {seller_username} -> {buyer_username} in new room #{room_info['room_num']}")

# =========================
# COMPLETE COMMAND (ONLY ADMIN)
# =========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg:
        return

    room_id = msg.chat_id
    user = msg.from_user

    escrow = get_active_escrow_by_room(room_id)

    if not escrow:
        await msg.reply_text("❌ No active escrow.")
        return
    
    if user.id != ADMIN_ID:
        await msg.reply_text("❌ Only Admin can complete this deal!")
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    remove_escrow(room_id, "completed", end_time, duration, completed_by="Admin")

    await msg.reply_text(
        f"✅ **DEAL COMPLETED BY ADMIN!**\n\n"
        f"⏱ Duration: {minutes}m {seconds}s\n\n"
        f"This room will be archived."
    )

    # Notify in main group
    try:
        main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
        await context.bot.send_message(
            chat_id=main_group_id,
            text=f"✅ **DEAL COMPLETED** in Escrow Room #{escrow['room_num']}!\n\n"
                 f"👤 Seller: @{escrow['seller_username']}\n"
                 f"👤 Buyer: @{escrow['buyer_username']}\n"
                 f"⏱ Duration: {minutes}m {seconds}s"
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
        await msg.reply_text("❌ No active escrow.")
        return
    
    is_seller = (user.id == escrow["seller_id"])
    is_buyer = (user.id == escrow["buyer_id"])
    is_admin = (user.id == ADMIN_ID)
    
    if not (is_seller or is_buyer or is_admin):
        await msg.reply_text("❌ Only Seller, Buyer, or Admin can cancel!")
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60
    
    if is_seller:
        cancelled_by = "Seller"
    elif is_buyer:
        cancelled_by = "Buyer"
    else:
        cancelled_by = "Admin"

    remove_escrow(room_id, "cancelled", end_time, duration, completed_by=cancelled_by)

    await msg.reply_text(
        f"❌ **DEAL CANCELLED!**\n\n"
        f"⏱ Duration: {minutes}m {seconds}s\n"
        f"❌ Cancelled by: {cancelled_by}\n\n"
        f"This room will be archived."
    )

    # Notify in main group
    try:
        main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
        await context.bot.send_message(
            chat_id=main_group_id,
            text=f"❌ **DEAL CANCELLED** in Escrow Room #{escrow['room_num']}!\n\n"
                 f"👤 Seller: @{escrow['seller_username']}\n"
                 f"👤 Buyer: @{escrow['buyer_username']}\n"
                 f"⏱ Duration: {minutes}m {seconds}s\n"
                 f"❌ Cancelled by: {cancelled_by}"
        )
    except Exception as e:
        logger.error(f"Failed to send cancellation notice: {e}")

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
                    "Payment method: ?"
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

    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
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
