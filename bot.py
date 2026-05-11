import re
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatMemberStatus

BOT_TOKEN = "8614020088:AAHQm6K7W2Il-ubOUxV2QMp3IWSnZFdL2PQ"
GROUP_ID = "@escrow_only_usdt"

logging.basicConfig(level=logging.INFO)

def is_valid(text):
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

async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    try:
        member = await context.bot.get_chat_member(msg.chat_id, msg.from_user.id)
        if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return
    except:
        pass
    if not is_valid(msg.text):
        try:
            await msg.delete()
        except:
            pass

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_msg))
    app.run_polling()

if __name__ == "__main__":
    main()
