import os
import re
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# Render pe environment variables set karna: API_TOKEN
API_TOKEN = os.getenv('API_TOKEN')

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Tera Required Format
REQUIRED_KEYWORDS = ["#Selling", "#buying", "Chain:", "Amount[USDT]:", "Amount[INR]:", "Rate[INR/USDT]:", "Payment Method:"]

@dp.message_handler(chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def filter_messages(message: types.Message):
    # Agar message me saare keywords nahi hain, toh delete kar do
    msg_text = message.text if message.text else ""
    
    # Check if all keywords are present
    if not all(keyword.lower() in msg_text.lower() for keyword in REQUIRED_KEYWORDS):
        try:
            await message.delete()
            # Member ko warning dena
            warning_text = (
                f"⚠️ @{message.from_user.username}, rules follow kar re saale!\n\n"
                "Sirf is format mein post kar sakte ho:\n\n"
                "#Selling/buying\n"
                "Chain: BSC\n"
                "Amount[USDT]: ?\n"
                "Amount[INR]: ?\n"
                "Rate[INR/USDT]: ?\n"
                "Payment Method: ?"
            )
            await message.answer(warning_text)
        except Exception as e:
            print(f"Error: {e}")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
