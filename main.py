import sys
import asyncio
import logging
import logging.handlers as handlers

from FileStream.server import web_server

logging.basicConfig(
    level=logging.INFO,
    datefmt="%d/%m/%Y %H:%M:%S",
    format='[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(stream=sys.stdout),
    ],
)

logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)

# This 'app' object is what Vercel looks for
app = web_server()

async def init_bot():
    """Initialize the Telegram bot on startup"""
    from FileStream.config import Telegram, Server
    from FileStream.bot import FileStream
    from FileStream.bot.clients import initialize_clients
    from pyrogram import idle

    await FileStream.start()
    bot_info = await FileStream.get_me()
    FileStream.id = bot_info.id
    FileStream.username = bot_info.username
    FileStream.fname = bot_info.first_name
    logging.info(f"Bot started: {bot_info.first_name}")
    await initialize_clients()
    logging.info("Clients initialized")

async def on_startup(application):
    asyncio.create_task(init_bot())

app.on_startup.append(on_startup)
