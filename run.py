



# run.py
import asyncio
import logging
from bot import bot  # Головний об'єкт бота

async def main():
    try:
        # Завантаження розширень
        await bot.load_extension("extensions.admin")
        await bot.load_extension("extensions.moderation")
        await bot.load_extension("extensions.user")
        await bot.load_extension("commands.music")
        await bot.load_extension("commands.help")
        logging.info("Усі розширення завантажено успішно.")
        await bot.start(bot.token)
    except Exception as e:
        logging.critical(f"Помилка запуску бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())



