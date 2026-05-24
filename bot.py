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

GROUP_ID = GROUP_ID_INPUT

# =========================
# ESCROW ROOMS
# =========================

ROOMS = [
    {
        "room_num": 1,
        "room_id": -1003974490347,
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
            room_id INTEGER,
            room_num INTEGER,
            start_time REAL,
            seller_id INTEGER
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
        SELECT 1 FROM active_escrows
        WHERE seller_username=? OR buyer_username=?
    """, (username, username))

    result = c.fetchone()

    conn.close()

    return result is not None

def add_active_escrow(
    seller_username,
    buyer_username,
    room_id,
    room_num,
    start_time,
    seller_id
):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        INSERT INTO active_escrows
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        seller_username,
        buyer_username,
        room_id,
        room_num,
        start_time,
        seller_id
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
        "room_id": result[2],
        "room_num": result[3],
        "start_time": result[4],
        "seller_id": result[5]
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
            escrow[3],
            escrow[4],
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
    "dm",
    "hi",
    "hello",
    "check dm",
    "done",
    "paid",
    "sent",
    "ok",
    "yes",
    "available"
]

# =========================
# GET ID COMMAND
# =========================

async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg:
        return

    await msg.reply_text(
        f"🆔 Chat ID:\n\n{msg.chat_id}"
    )

# =========================
# ESCROW COMMAND
# =========================

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    if not msg:
        return

    if len(context.args) < 1:

        await msg.reply_text(
            "❌ Usage:\n\n/escrow @username"
        )

        return

    seller = msg.from_user

    if not seller.username:

        await msg.reply_text(
            "❌ Please set Telegram username first!"
        )

        return

    seller_username = seller.username.lower()
    buyer_username = context.args[0].replace("@", "").lower()

    if seller_username == buyer_username:

        await msg.reply_text(
            "❌ You cannot escrow with yourself!"
        )

        return

    if has_active_escrow(seller_username):

        await msg.reply_text(
            f"❌ @{seller_username} already has active escrow!"
        )

        return

    if has_active_escrow(buyer_username):

        await msg.reply_text(
            f"❌ @{buyer_username} already has active escrow!"
        )

        return

    room = get_free_room()

    if not room:

        await msg.reply_text(
            "❌ All escrow rooms are busy!"
        )

        return

    room_id = room["room_id"]

    try:

        await context.bot.set_chat_title(
            chat_id=room_id,
            title=f"Escrow #{room['room_num']} | @{seller_username} x @{buyer_username}"
        )

    except Exception as e:
        logger.error(f"Title change error: {e}")

    start_time = time.time()

    add_active_escrow(
        seller_username,
        buyer_username,
        room["room_id"],
        room["room_num"],
        start_time,
        seller.id
    )

    # SEND MESSAGE IN ROOM

    try:

        await context.bot.send_message(
            chat_id=room["room_id"],
            text=(
                f"🏦 ESCROW STARTED\n\n"
                f"👤 Seller: @{seller_username}\n"
                f"👤 Buyer: @{buyer_username}\n\n"
                f"📝 Commands:\n"
                f"/complete\n"
                f"/cancel"
            )
        )

    except Exception as e:
        logger.error(f"Room message error: {e}")

    # SEND LINK TO SELLER

    seller_text = (
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Buyer: @{buyer_username}\n"
        f"🔗 Join Link:\n{room['invite_link']}\n\n"
        f"📝 Commands:\n"
        f"/complete\n"
        f"/cancel"
    )

    try:

        await context.bot.send_message(
            chat_id=seller.id,
            text=seller_text
        )

    except Exception as e:
        logger.error(f"Seller DM error: {e}")

    # GROUP CONFIRMATION

    await msg.reply_text(
        f"✅ Escrow Room #{room['room_num']} Created!\n\n"
        f"👤 Seller: @{seller_username}\n"
        f"👤 Buyer: @{buyer_username}\n\n"
        f"📩 Room link sent in DM."
    )

    logger.info(
        f"Escrow created: {seller_username} -> {buyer_username}"
    )

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

        await msg.reply_text(
            "❌ No active escrow."
        )

        return

    user = msg.from_user

    if user.id != escrow["seller_id"] and user.id != ADMIN_ID:

        await msg.reply_text(
            "❌ Only seller or admin can complete!"
        )

        return

    end_time = time.time()

    duration = int(end_time - escrow["start_time"])

    minutes = duration // 60
    seconds = duration % 60

    remove_escrow(
        room_id,
        "completed",
        end_time,
        duration
    )

    free_room(room_id)

    try:

        await context.bot.set_chat_title(
            chat_id=room_id,
            title="AVAILABLE ESCROW ROOM"
        )

    except Exception as e:
        logger.error(e)

    await msg.reply_text(
        f"✅ Deal Completed!\n\n"
        f"⏱ Duration: {minutes}m {seconds}s"
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

        await msg.reply_text(
            "❌ No active escrow."
        )

        return

    user = msg.from_user

    if user.id != escrow["seller_id"] and user.id != ADMIN_ID:

        await msg.reply_text(
            "❌ Only seller or admin can cancel!"
        )

        return

    end_time = time.time()

    duration = int(end_time - escrow["start_time"])

    minutes = duration // 60
    seconds = duration % 60

    remove_escrow(
        room_id,
        "cancelled",
        end_time,
        duration
    )

    free_room(room_id)

    try:

        await context.bot.set_chat_title(
            chat_id=room_id,
            title="AVAILABLE ESCROW ROOM"
        )

    except Exception as e:
        logger.error(e)

    await msg.reply_text(
        f"❌ Deal Cancelled!\n\n"
        f"⏱ Duration: {minutes}m {seconds}s"
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

    if (
        chat_username != target_group
        and str(chat.id) != str(GROUP_ID)
    ):
        return

    try:

        member = await context.bot.get_chat_member(
            msg.chat_id,
            msg.from_user.id
        )

        if member.status in [
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER
        ]:
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

    # COMMANDS

    app.add_handler(
        CommandHandler("getid", getid_command)
    )

    app.add_handler(
        CommandHandler("escrow", escrow_command)
    )

    app.add_handler(
        CommandHandler("complete", complete_command)
    )

    app.add_handler(
        CommandHandler("cancel", cancel_command)
    )

    # FILTER

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

    bot_thread = threading.Thread(
        target=run_bot,
        daemon=True
    )

    bot_thread.start()

    flask_app.run(
        host="0.0.0.0",
        port=PORT
            )
