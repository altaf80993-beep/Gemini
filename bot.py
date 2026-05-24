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
        r"AmountUSDT:\s*.+[\r\n]+"
        r"AmountINR:\s*.+[\r\n]+"
        r"RateINR/USDT:\s*.+[\r\n]+"
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
