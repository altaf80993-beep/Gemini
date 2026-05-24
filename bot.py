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
ADMIN_ID = int(os.getenv("ADMIN_ID", "6722137021"))
ADMIN_USERNAME = "@crypto_8099"
PORT = int(os.getenv("PORT", 10000))

GROUP_ID = GROUP_ID_INPUT

# Store active rooms in memory
ACTIVE_ROOMS: Dict[int, dict] = {}

# =========================
# ESCROW ROOMS (Pre-created)
# =========================

ROOMS = [
    {
        "room_num": 1,
        "room_id": --1003970953090,
        "invite_link": "https://t.me/+_5lw9u-sBM0xY2Jl",
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
        await msg.reply_text("❌ All escrow rooms are busy! Please wait.")
        return

    room_id = room["room_id"]

    # Get buyer ID
    buyer_id = 0
    try:
        buyer_chat = await context.bot.get_chat(f"@{buyer_username}")
        buyer_id = buyer_chat.id
    except Exception as e:
        logger.error(f"Could not fetch buyer ID: {e}")

    # NO TITLE CHANGE - prevents group deletion
    # try:
    #     await context.bot.set_chat_title(
    #         chat_id=room_id,
    #         title=f"Escrow #{room['room_num']} | @{seller_username} x @{buyer_username}"
    #     )
    # except Exception as e:
    #     logger.error(f"Title change error: {e}")

    start_time = time.time()

    # Get permanent invite link
    invite_link = room.get("invite_link")
    if not invite_link:
        try:
            link_obj = await context.bot.create_chat_invite_link(
                chat_id=room_id,
                member_limit=2,
                expire_date=0  # Never expires!
            )
            invite_link = link_obj.invite_link
            room["invite_link"] = invite_link
        except:
            try:
                invite_link = await context.bot.export_chat_invite_link(chat_id=room_id)
                room["invite_link"] = invite_link
            except:
                invite_link = room["invite_link"]

    add_active_escrow(
        seller_username,
        buyer_username,
        seller.id,
        buyer_id,
        room["room_id"],
        room["room_num"],
        start_time,
        invite_link
    )

    # SEND MESSAGE IN ESCROW ROOM
    try:
        await context.bot.send_message(
            chat_id=room["room_id"],
            text=(
                f"🔒 **ESCROW STARTED**\n\n"
                f"👤 Seller: @{seller_username}\n"
                f"👤 Buyer: @{buyer_username}\n"
                f"🆔 Room #{room['room_num']}\n\n"
                f"⚠️ **IMPORTANT RULES:**\n"
                f"• ONLY ADMIN {ADMIN_USERNAME} can complete or cancel\n"
                f"• Seller CANNOT cancel (USDT is locked with admin)\n"
                f"• Buyer CANNOT cancel (payment is secure)\n\n"
                f"📝 **PROCESS:**\n"
                f"1. Buyer sends payment to seller\n"
                f"2. Seller confirms payment to admin\n"
                f"3. Admin releases USDT using /complete\n\n"
                f"⚠️ For any disputes, contact admin directly."
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Room message error: {e}")

    # MAIN GROUP MESSAGE WITH LINK
    await msg.reply_text(
        f"🔒 **ESCROW ROOM #{room['room_num']}**\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"🔗 **Join Link:**\n{invite_link}\n\n"
        f"⚠️ **Beware of Scammers** ⚠️\n\n"
        f"📝 **Important Rules:**\n"
        f"• ONLY ADMIN {ADMIN_USERNAME} can CONFIRM THEN SEND YOUR ASSETS\n"
        f"• DON'T SEND USDT TO BUYER\n\n"
        f"• DON'T SEND PAYMENT TO SELLER\n\n"
        f"• without admin confifornation sending any assets admin will not responsible\n"
    )

    # Send backup link to seller DM
    try:
        await context.bot.send_message(
            chat_id=seller.id,
            text=(
                f"✅ **Escrow Room #{room['room_num']} Created!**\n\n"
                f"👤 Buyer: @{buyer_username}\n\n"
                f"🔗 **Join Link:**\n{invite_link}\n\n"
                f"⚠️ **Share this link with buyer!**\n\n"
                f"📝 **Process:**\n"
                f"1. Buyer joins room using link\n"
                f"2. Buyer sends payment to you\n"
                f"3. You confirm payment to admin\n"
                f"4. Admin releases USDT using /complete\n\n"
                f"⚠️ **You cannot cancel this deal - only admin can!**"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Seller DM error: {e}")

    logger.info(f"Escrow created: {seller_username} -> {buyer_username} in room #{room['room_num']}")

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
    
    # ONLY ADMIN can complete
    if user.id != ADMIN_ID:
        await msg.reply_text(
            f"❌ **Only Admin {ADMIN_USERNAME} can complete this deal!**\n\n"
            f"Seller (@{escrow['seller_username']}) and Buyer (@{escrow['buyer_username']}) cannot use /complete.\n\n"
            f"Contact admin to complete the deal."
        )
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    seller_username = escrow['seller_username']
    buyer_username = escrow['buyer_username']
    room_num = escrow['room_num']

    remove_escrow(room_id, "completed", end_time, duration, completed_by="Admin")
    free_room(room_id)

    # NO TITLE CHANGE - prevents group deletion
    # try:
    #     await context.bot.set_chat_title(
    #         chat_id=room_id,
    #         title="AVAILABLE ESCROW ROOM"
    #     )
    # except Exception as e:
    #     logger.error(e)

    # Send completion message in escrow room
    await msg.reply_text(
        f"✅ **DEAL COMPLETED BY ADMIN!**\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n"
        f"⏱ Duration: {minutes}m {seconds}s\n\n"
        f"🟢 **Room is now available for new escrow.**\n"
        f"Use /escrow @username in main group to start new deal."
    )

    # Notify in main group
    try:
        main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
        await context.bot.send_message(
            chat_id=main_group_id,
            text=f"✅ **DEAL COMPLETED** in Escrow Room #{room_num}!\n\n"
                 f"👤 Seller: @{seller_username}\n"
                 f"👤 Buyer: @{buyer_username}\n"
                 f"⏱ Duration: {minutes}m {seconds}s\n\n"
                 f"✅ Deal completed by Admin {ADMIN_USERNAME}."
        )
    except Exception as e:
        logger.error(f"Failed to send completion notice: {e}")

    logger.info(f"Escrow completed by Admin: {seller_username} -> {buyer_username}")

# =========================
# CANCEL COMMAND (ONLY ADMIN)
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
    
    # ONLY ADMIN can cancel
    if user.id != ADMIN_ID:
        await msg.reply_text(
            f"❌ **Only Admin {ADMIN_USERNAME} can cancel this deal!**\n\n"
            f"Seller (@{escrow['seller_username']}) and Buyer (@{escrow['buyer_username']}) cannot cancel.\n\n"
            f"If there is an issue, please contact admin."
        )
        return

    end_time = time.time()
    duration = int(end_time - escrow["start_time"])
    minutes = duration // 60
    seconds = duration % 60

    seller_username = escrow['seller_username']
    buyer_username = escrow['buyer_username']
    room_num = escrow['room_num']

    remove_escrow(room_id, "cancelled", end_time, duration, completed_by="Admin")
    free_room(room_id)

    # NO TITLE CHANGE - prevents group deletion
    # try:
    #     await context.bot.set_chat_title(
    #         chat_id=room_id,
    #         title="AVAILABLE ESCROW ROOM"
    #     )
    # except Exception as e:
    #     logger.error(e)

    # Send cancellation message in escrow room
    await msg.reply_text(
        f"❌ **DEAL CANCELLED BY ADMIN!**\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n"
        f"⏱ Duration: {minutes}m {seconds}s\n\n"
        f"🟢 **Room is now available for new escrow.**\n"
        f"Use /escrow @username in main group to start new deal."
    )

    # Notify in main group
    try:
        main_group_id = int(GROUP_ID) if str(GROUP_ID).lstrip('-').isdigit() else GROUP_ID
        await context.bot.send_message(
            chat_id=main_group_id,
            text=f"❌ **DEAL CANCELLED BY ADMIN** in Escrow Room #{room_num}!\n\n"
                 f"👤 Seller: @{seller_username}\n"
                 f"👤 Buyer: @{buyer_username}\n"
                 f"⏱ Duration: {minutes}m {seconds}s\n\n"
                 f"❌ Deal cancelled by Admin {ADMIN_USERNAME}."
        )
    except Exception as e:
        logger.error(f"Failed to send cancellation notice: {e}")

    logger.info(f"Escrow cancelled by Admin: {seller_username} -> {buyer_username}")

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
