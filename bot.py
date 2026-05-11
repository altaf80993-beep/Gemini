import re
import os
import logging
import threading
import asyncio

from flask import Flask
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatMemberStatus

# =========================
# LOAD ENV
# =========================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = os.getenv("GROUP_ID", "-1001234567890")
PORT = int(os.getenv("PORT", 10000))

# =========================
# LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

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
# TELEGRAM FILTER
# =========================
async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message

    # Ignore empty messages
    if not msg or not msg.text:
        return

    chat = msg.chat

    # =========================
    # ONLY WORK IN YOUR GROUP
    # =========================
    chat_username = (chat.username or "").lower()
    target_group = GROUP_ID.lstrip("@").lower()

    if (
        chat_username != target_group
        and str(chat.id) != GROUP_ID
    ):
        return

    # =========================
    # ALLOW ADMINS
    # =========================
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
        logger.error(f"Member check error: {e}")

    # =========================
    # DELETE INVALID MESSAGE
    # =========================
    if not is_valid(msg.text):

        try:
            await msg.delete()

            logger.info(
                f"Deleted invalid message from "
                f"{msg.from_user.username or msg.from_user.first_name}"
            )

        except Exception as e:
            logger.error(f"Delete error: {e}")

# =========================
# FLASK APP
# =========================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot Running Successfully!"

# =========================
# START TELEGRAM BOT
# =========================
async def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    # Message filter handler
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            filter_msg
        )
    )

    logger.info("Telegram bot started...")

    # Start bot properly
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Keep alive loop
    while True:
        await asyncio.sleep(100)

# =========================
# RUN BOT IN THREAD
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

    # Telegram Bot Thread
    bot_thread = threading.Thread(
        target=run_bot,
        daemon=True
    )

    bot_thread.start()

    # Flask Web Server
    flask_app.run(
        host="0.0.0.0",
        port=PORT
    )

Abhi sahi hai na
