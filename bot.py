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

=========================

LOAD ENV

=========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

GROUP_ID = os.getenv("GROUP_ID")

ADMIN_ID = int(os.getenv("ADMIN_ID"))

PORT = int(os.getenv("PORT", 10000))

=========================

ESCROW ROOMS

=========================

ROOMS = [
{
"room_num": 1,
"room_id": -1001111111111,  # Replace with real room ID
"invite_link": "https://t.me/+FbR1VOqqXqswYjJl",
"busy": False
},

{  
    "room_num": 2,  
    "room_id": -1002222222222,  # Replace with real room ID  
    "invite_link": "https://t.me/+_5lw9u-sBM0xY2Jl",  
    "busy": False  
}

]

=========================

LOGGING

=========================

logging.basicConfig(
format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
level=logging.INFO
)

logger = logging.getLogger(name)

=========================

DATABASE

=========================

DB_NAME = "database.db"

def init_db():

conn = sqlite3.connect(DB_NAME)  

c = conn.cursor()  

c.execute("""  
    CREATE TABLE IF NOT EXISTS active_escrows (  
        seller_username TEXT,  
        buyer_username TEXT,  
        room_id INTEGER,  
        room_num INTEGER,  
        start_time REAL  
    )  
""")  

conn.commit()  
conn.close()

def has_active_escrow(username):

conn = sqlite3.connect(DB_NAME)  

c = conn.cursor()  

c.execute("""  
    SELECT * FROM active_escrows  
    WHERE seller_username=?  
    OR buyer_username=?  
""", (username, username))  

result = c.fetchone()  

conn.close()  

return result is not None

def add_active_escrow(
seller_username,
buyer_username,
room_id,
room_num,
start_time
):

conn = sqlite3.connect(DB_NAME)  

c = conn.cursor()  

c.execute("""  
    INSERT INTO active_escrows  
    VALUES (?, ?, ?, ?, ?)  
""", (  
    seller_username,  
    buyer_username,  
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
    "room_id": result[2],  
    "room_num": result[3],  
    "start_time": result[4]  
}

def remove_escrow(room_id):

conn = sqlite3.connect(DB_NAME)  

c = conn.cursor()  

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

=========================

MESSAGE FORMAT CHECK

=========================

def is_valid(text: str) -> bool:

pattern = re.compile(  
    r"^(#buying|#selling)\s*[\r\n]+"  
    r"Chain:\s*.+[\r\n]+"  
    r"AmountUSDT:\s*.+[\r\n]+"  
    r"AmountINR:\s*.+[\r\n]+"  
    r"RateINR/USDT:\s*.+[\r\n]+"  
    r"Payment method:\s*.+",  
    re.IGNORECASE  
)  

return bool(pattern.match(text.strip()))

=========================

ALLOWED SHORT MESSAGES

=========================

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
"available",
]

=========================

TELEGRAM FILTER

=========================

async def filter_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):

msg = update.message  

if not msg or not msg.text:  
    return  

chat = msg.chat  

chat_username = (chat.username or "").lower()  

target_group = str(GROUP_ID).replace("@", "").lower()  

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
    logger.error(f"Member check error: {e}")  

text_lower = msg.text.strip().lower()  

if text_lower in ALLOWED_MESSAGES:  
    return  

if not is_valid(msg.text):  

    try:  

        await msg.delete()  

        await context.bot.send_message(  
            chat_id=msg.chat_id,  
            text=(  
                "❌ Invalid post format\n\n"  
                "Use this format:\n\n"  
                "#buying or #selling\n\n"  
                "Chain: BEP20\n"  
                "Amount[USDT]: ?\n"  
                "Amount[INR]: ?\n"  
                "Rate[INR/USDT]: ?\n"  
                "Payment method: ?\n"  
                "Escrow fee: buyer/seller"  
            )  
        )  

    except Exception as e:  
        logger.error(f"Delete error: {e}")

=========================

ESCROW COMMAND

=========================
# =========================
# ID COMMAND
# =========================

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        f"Chat ID:\n{update.effective_chat.id}"
    )

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

msg = update.message  

if not msg:  
    return  

if len(context.args) < 1:  

    await msg.reply_text(  
        "❌ Usage:\n/escrow @username"  
    )  

    return  

seller = msg.from_user  

if not seller.username:  

    await msg.reply_text(  
        "❌ You need a Telegram username."  
    )  

    return  

seller_username = seller.username.lower()  

buyer_username = context.args[0].replace("@", "").lower()  

if seller_username == buyer_username:  

    await msg.reply_text(  
        "❌ You cannot escrow with yourself."  
    )  

    return  

if has_active_escrow(seller_username):  

    await msg.reply_text(  
        f"❌ @{seller_username} already has an active escrow deal!"  
    )  

    return  

if has_active_escrow(buyer_username):  

    await msg.reply_text(  
        f"❌ @{buyer_username} already has an active escrow deal!"  
    )  

    return  

room = get_free_room()  

if not room:  

    await msg.reply_text(  
        "❌ No escrow rooms available."  
    )  

    return  

room_id = room["room_id"]  

room_num = room["room_num"]  

invite_link = room["invite_link"]  

try:  

    await context.bot.set_chat_title(  
        chat_id=room_id,  
        title=f"Escrow #{room_num} | @{seller_username} x @{buyer_username}"  
    )  

    start_time = time.time()  

    add_active_escrow(  
        seller_username,  
        buyer_username,  
        room_id,  
        room_num,  
        start_time  
    )  

    await context.bot.send_message(  
        chat_id=room_id,  
        text=(  
            f"🏦 Escrow Started\n\n"  
            f"👤 Seller: @{seller_username}\n"  
            f"👤 Buyer: @{buyer_username}\n\n"  
            f"Commands:\n"  
            f"/complete\n"  
            f"/cancel"  
        )  
    )  

    await msg.reply_text(  
        f"✅ Escrow Room #{room_num} Created\n\n"  
        f"Seller: @{seller_username}\n"  
        f"Buyer: @{buyer_username}\n\n"  
        f"🔗 Join Link:\n"  
        f"{invite_link}"  
    )  

except Exception as e:  

    free_room(room_id)  

    logger.error(e)  

    await msg.reply_text(  
        f"❌ Error:\n{str(e)}"  
    )

=========================

COMPLETE COMMAND

=========================

async def complete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

msg = update.message  

room_id = msg.chat_id  

escrow = get_active_escrow_by_room(room_id)  

if not escrow:  

    await msg.reply_text(  
        "❌ No active escrow."  
    )  

    return  

remove_escrow(room_id)  

free_room(room_id)  

await context.bot.set_chat_title(  
    room_id,  
    "AVAILABLE ESCROW ROOM"  
)  

await msg.reply_text(  
    "✅ Deal Completed"  
)

=========================

CANCEL COMMAND

=========================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

msg = update.message  

room_id = msg.chat_id  

escrow = get_active_escrow_by_room(room_id)  

if not escrow:  

    await msg.reply_text(  
        "❌ No active escrow."  
    )  

    return  

remove_escrow(room_id)  

free_room(room_id)  

await context.bot.set_chat_title(  
    room_id,  
    "AVAILABLE ESCROW ROOM"  
)  

await msg.reply_text(  
    "❌ Deal Cancelled"  
)

=========================

FLASK APP

=========================

flask_app = Flask(name)

@flask_app.route("/")
def home():
return "Bot Running Successfully!"

=========================

START TELEGRAM BOT

=========================

async def start_bot():

init_db()  

app = Application.builder().token(BOT_TOKEN).build()  

# Message Filter  
app.add_handler(  
    MessageHandler(  
        filters.TEXT & ~filters.COMMAND,  
        filter_msg  
    )  
)  

# Escrow Commands  
app.add_handler(  
  app.add_handler(
    CommandHandler("id", id_command)
) CommandHandler("escrow", escrow_command)  
)  

app.add_handler(  
    CommandHandler("complete", complete_command)  
)  

app.add_handler(  
    CommandHandler("cancel", cancel_command)  
)  

logger.info("Telegram bot started...")  

await app.initialize()  

await app.start()  

await app.updater.start_polling()  

while True:  
    await asyncio.sleep(100)

=========================

RUN BOT THREAD

=========================

def run_bot():

loop = asyncio.new_event_loop()  

asyncio.set_event_loop(loop)  

loop.run_until_complete(start_bot())

=========================

MAIN

=========================

if name == "main":

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
