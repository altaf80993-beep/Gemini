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

GROUP_ID = os.getenv("GROUP_ID")

ADMIN_ID = int(os.getenv("ADMIN_ID"))

PORT = int(os.getenv("PORT", 10000))

# =========================
# ESCROW ROOMS
# =========================

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

# =========================
# LOGGING
