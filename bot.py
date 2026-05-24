import re
import os
import time
import sqlite3
import logging
import threading
import asyncio

from flask import Flask
from dotenv import load_dotenv

from telegram import Update, ChatMember
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
GROUP_ID_INPUT = os.getenv("GROUP_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", 10000))

GROUP_ID = GROUP_ID_INPUT

# =========================
# ESCROW ROOMS
# =========================

ROOMS = [
    {
        "room_num": 1,
        "room_id": -1003974490347,
        "busy": False
    },
    {
        "room_num": 2,
        "room_id": -1003766531525,
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
            start_time REAL
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

    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_joins (
            user_id INTEGER,
            username TEXT,
            room_id INTEGER,
            request_time REAL
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
    start_time
):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        INSERT INTO active_escrows
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        seller_username,
        buyer_username,
        seller_id,
        buyer_id,
        room_id,
        room_num,
        start_time
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
        "start_time": result[6]
    }

def get_active_escrow_by_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        SELECT * FROM active_escrows
        WHERE seller_id=? OR buyer_id=?
    """, (user_id, user_id))

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
        "start_time": result[6]
    }

def remove_escrow(room_id, status, end_time, duration):
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
                duration_seconds
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            escrow[0],
            escrow[1],
            escrow[5],
            escrow[6],
            end_time,
            status,
            duration
        ))

        c.execute("""
            DELETE FROM active_escrows
            WHERE room_id=?
        """, (room_id,))

    conn.commit()
    conn.close()

def add_pending_join(user_id, username, room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        INSERT INTO pending_joins
        VALUES (?, ?, ?, ?)
    """, (user_id, username, room_id, time.time()))
    
    conn.commit()
    conn.close()

def is_pending_join(user_id, room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        SELECT 1 FROM pending_joins
        WHERE user_id=? AND room_id=?
    """, (user_id, room_id))
    
    result = c.fetchone()
    conn.close()
    
    return result is not None

def remove_pending_join(user_id, room_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""
        DELETE FROM pending_joins
        WHERE user_id=? AND room_id=?
    """, (user_id, room_id))
    
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
# GENERATE INVITE LINK
# =========================

async def get_invite_link(context: ContextTypes.DEFAULT_TYPE, room_id: int) -> str:
    """Generate an invite link for the room"""
    try:
        # Create invite link that requires admin approval
        invite_link = await context.bot.create_chat_invite_link(
            chat_id=room_id,
            member_limit=1,
            expire_date=int(time.time()) + 3600,
            creates_join_request=True  # KEY: This makes users request to join
        )
        return invite_link.invite_link
    except Exception as e:
        logger.error(f"Failed to create invite link: {e}")
        try:
            invite_link = await context.bot.export_chat_invite_link(chat_id=room_id)
            return invite_link
        except Exception as e2:
            logger.error(f"Failed to export invite link: {e2}")
            return f"❌ Failed to generate link."

# =========================
# HANDLE JOIN REQUESTS
# =========================

async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when someone requests to join or joins the room"""
    
    if not update.chat_member:
        return
    
    chat_member_update = update.chat_member
    
    chat_id = chat_member_update.chat.id
    user = chat_member_update.new_chat_member.user
    user_id = user.id
    username = user.username or user.first_name
    
    # Check if this is an escrow room
    room = None
    for r in ROOMS:
        if r["room_id"] == chat_id:
            room = r
            break
    
    if not room:
        return
    
    old_status = chat_member_update.old_chat_member.status
    new_status = chat_member_update.new_chat_member.status
    
    # User requested to join
    if new_status == ChatMemberStatus.RESTRICTED and old_status != ChatMemberStatus.RESTRICTED:
        
        escrow = get_active_escrow_by_room(chat_id)
        
        if not escrow:
            # No active escrow, deny join
            try:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                logger.info(f"Denied join request from {username} - no active escrow")
            except Exception as e:
                logger.error(f"Failed to deny join: {e}")
            return
        
        # Check if user is seller or buyer
        is_authorized = (
            user_id == escrow["seller_id"] or 
            user_id == escrow["buyer_id"] or 
            user_id == ADMIN_ID
        )
        
        if is_authorized:
            # Approve join request
            try:
                await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
                logger.info(f"Approved join request for {username} in room {room['room_num']}")
                
                # Notify in room
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ @{username} has joined the escrow room!"
                )
            except Exception as e:
                logger.error(f"Failed to approve join: {e}")
        else:
            # Deny join request - unauthorized user
            try:
                await context.bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
                logger.info(f"Denied join request from unauthorized user {username}")
                
                # Try to DM the user
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"❌ Join request denied!\n\nThis is a private escrow room. Only @{escrow['seller_username']} and @{escrow['buyer_username']} can join."
                    )
                except:
                    pass
            except Exception as e:
                logger.error(f"Failed to decline join: {e}")
    
    # User left the room
    elif old_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR] and new_status == ChatMemberStatus.LEFT:
        escrow = get_active_escrow_by_room(chat_id)
        if escrow:
            user_role = "Seller" if user_id == escrow["seller_id"] else "Buyer" if user_id == escrow["buyer_id"] else "Unknown"
            logger.info(f"{user_role} ({username}) left escrow room {room['room_num']}")

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
# GET ID COMMAND
# =========================

async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    await msg.reply_text(f"🆔 Chat ID:\n\n{msg.chat_id}")

# =========================
# ESCROW COMMAND
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

    room = get_free_room()
    if not room:
        await msg.reply_text("❌ All escrow rooms are busy!")
        return

    room_id = room["room_id"]

    # Try to get buyer's user ID
    buyer_id = None
    try:
        # Try to get chat from username
        buyer_chat = await context.bot.get_chat(f"@{buyer_username}")
        buyer_id = buyer_chat.id
    except:
        buyer_id = None

    try:
        await context.bot.set_chat_title(
            chat_id=room_id,
            title=f"🔒 Escrow #{room['room_num']} | @{seller_username} x @{buyer_username}"
        )
    except Exception as e:
        logger.error(f"Title change error: {e}")

    start_time = time.time()

    add_active_escrow(
        seller_username,
        buyer_username,
        seller.id,
        buyer_id if buyer_id else 0,
        room["room_id"],
        room["room_num"],
        start_time
    )

    # Generate invite link with join request
    invite_link = await get_invite_link(context, room["room_id"])

    # Send message in escrow room
    try:
        await context.bot.send_message(
            chat_id=room["room_id"],
            text=(
                f"🔒 PRIVATE ESCROW STARTED\n\n"
                f"👤 Seller: @{seller_username}\n"
                f"👤 Buyer: @{buyer_username}\n\n"
                f"⚠️ ONLY seller, buyer and admin can join!\n"
                f"⚠️ Others will be automatically denied.\n\n"
                f"📝 Commands:\n"
                f"/complete - Complete deal\n"
                f"/cancel - Cancel deal"
            )
        )
    except Exception as e:
        logger.error(f"Room message error: {e}")

    # Send link to seller (DM)
    seller_text = (
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"🔗 INVITE LINK (Share ONLY with buyer):\n{invite_link}\n\n"
        f"⚠️ IMPORTANT:\n"
        f"• ONLY you and buyer can join!\n"
        f"• Buyer will need to request join - bot will auto-approve\n"
        f"• Link expires in 1 hour\n"
        f"• Do NOT share with anyone else!\n\n"
        f"📝 Commands (use in escrow room):\n"
        f"/complete - Complete deal\n"
        f"/cancel - Cancel deal"
    )

    try:
        await context.bot.send_message(
            chat_id=seller.id,
            text=seller_text
        )
    except Exception as e:
        logger.error(f"Seller DM error: {e}")

    # Send confirmation in main group
    await msg.reply_text(
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"🔗 Room invite link sent to seller in DM.\n"
        f"⚠️ ONLY @{seller_username} and @{buyer_username} can join!\n\n"
        f"Buyer: Please contact seller for the join link."
    )

    logger.info(f"Escrow created: {seller_username} -> {buyer_username}")

# =========================
# COMPLETE COMMAND
# =========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    room_id = msg.chat_id
    escrow = get_active_escrow_by_room(room_id)

    if not escrow:
        await msg.reply_text("❌ No active escrow.")
        return

    user = msg.from_user
    if user.id != escrow["seller_id"] and user.id != ADMIN_ID:
        await msg.reply_text("❌ Only seller or admin can complete!")
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    remove_escrow(room_id, "completed", end_time, duration)
    free_room(room_id)

    try:
        await context.bot.set_chat_title(
            chat_id=room_id,
            title="✅ AVAILABLE ESCROW ROOM"
        )
    except Exception as e:
        logger.error(e)

    await msg.reply_text(
        f"✅ Deal Completed!\n\n⏱ Duration: {minutes}m {seconds}s\n\nRoom is now available for new escrow."
    )

# =========================
# CANCEL COMMAND
# =========================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    room_id = msg.chat_id
    escrow = get_active_escrow_by_room(room_id)

    if not escrow:
        await msg.reply_text("❌ No active escrow.")
        return

    user = msg.from_user
    if user.id != escrow["seller_id"] and user.id != ADMIN_ID:
        await msg.reply_text("❌ Only seller or admin can cancel!")
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    remove_escrow(room_id, "cancelled", end_time, duration)
    free_room(room_id)

    try:
        await context.bot.set_chat_title(
            chat_id=room_id,
            title="🔄 AVAILABLE ESCROW ROOM"
        )
    except Exception as e:
        logger.error(e)

    await msg.reply_text(
        f"❌ Deal Cancelled!\n\n⏱ Duration: {minutes}m {seconds}s\n\nRoom is now available for new escrow."
    )

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

    # Add handlers
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("complete", complete_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
    # Handle join requests
    app.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    
    # Message filter
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_msg))

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
