import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message

# Logging enable kar lo taaki error dikhe Render ke logs mein
logging.basicConfig(level=logging.INFO)

# Render Environment Variable se API_TOKEN lega
API_TOKEN = os.getenv('API_TOKEN')

# Bot aur Dispatcher initialize (Aiogram 3.x version)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Tera Fixed Format (Keywords check karne ke liye)
REQUIRED_KEYWORDS = [
    "#Selling", 
    "#buying", 
    "Chain:", 
    "Amount[USDT]:", 
    "Amount[INR]:", 
    "Rate[INR/USDT]:", 
    "Payment Method:"
]

# Message handler: Sirf Groups aur Supergroups ke liye
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def filter_group_messages(message: Message):
    # Agar message text hai ya caption (image ke saath)
    msg_text = message.text or message.caption or ""
    
    # Check if keywords are missing
    # (Hum check kar rahe hain ki format follow ho raha hai ya nahi)
    is_valid = any(kw.lower() in msg_text.lower() for kw in ["#selling", "#buying"]) and \
               all(kw.lower() in msg_text.lower() for kw in REQUIRED_KEYWORDS[2:])

    if not is_valid:
        try:
            # Galat message delete kar do
            await message.delete()
            
            # Member ko warning do
            warning = (
                f"⚠️ @{message.from_user.username}, Rules follow kar re saale!\n\n"
                "Sirf is format mein post kar sakte ho:\n\n"
                "#Selling/buying\n"
                "Chain: BSC\n"
                "Amount[USDT]: ?\n"
                "Amount[INR]: ?\n"
                "Rate[INR/USDT]: ?\n"
                "Payment Method: ?"
            )
            await message.answer(warning)
        except Exception as e:
            logging.error(f"Error handling message: {e}")

# Bot start karne ka function
async def main():
    logging.info("Sovereign Guard Bot is Starting...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped!")
