import asyncio
import logging
import sys
import traceback
import logging.handlers as handlers

from FileStream.config import Telegram, Server
from aiohttp import web
from pyrogram import idle

from FileStream.bot import FileStream
from FileStream.server import web_server
from FileStream.bot.clients import initialize_clients

logging.basicConfig(
    level=logging.INFO,
    datefmt="%d/%m/%Y %H:%M:%S",
    format='[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(stream=sys.stdout),
        handlers.RotatingFileHandler("streambot.log", mode="a", maxBytes=104857600, backupCount=2, encoding="utf-8")
    ],
)

logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)

# Create web application for ASGI servers
app = web_server()

# Alias for compatibility
application = app
handler = app

async def start_services():
    print()
    if Telegram.SECONDARY:
        print("------------------ Starting as Secondary Server ------------------")
    else:
        print("------------------- Starting as Primary Server -------------------")
    print()
    print("-------------------- Initializing Telegram Bot --------------------")

    await FileStream.start()
    bot_info = await FileStream.get_me()
    FileStream.id = bot_info.id
    FileStream.username = bot_info.username
    FileStream.fname = bot_info.first_name
    print("------------------------------ DONE ------------------------------")
    print()
    print("---------------------- Initializing Clients ----------------------")
    await initialize_clients()
    print("------------------------------ DONE ------------------------------")
    print()
    print("--------------------- Initializing Web Server ---------------------")
    
    server_runner = web.AppRunner(app)
    await server_runner.setup()
    await web.TCPSite(server_runner, Server.BIND_ADDRESS, Server.PORT).start()
    print("------------------------------ DONE ------------------------------")
    print()
    print("------------------------- Service Started -------------------------")
    print("                        bot =>> {}".format(bot_info.first_name))
    if bot_info.dc_id:
        print("                        DC ID =>> {}".format(str(bot_info.dc_id)))
    print(" URL =>> {}".format(Server.URL))
    print("------------------------------------------------------------------")
    
    # Store runner for cleanup
    global _server_runner
    _server_runner = server_runner
    
    await idle()

async def cleanup():
    if hasattr(start_services, '_server_runner'):
        await start_services._server_runner.cleanup()
    await FileStream.stop()

# Global variable for cleanup
_server_runner = None

def run_bot():
    """Function to run the bot as standalone"""
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        pass
    except Exception as err:
        logging.error(traceback.format_exc())
    finally:
        if _server_runner:
            loop.run_until_complete(_server_runner.cleanup())
        loop.run_until_complete(FileStream.stop())
        loop.stop()
        print("------------------------ Stopped Services ------------------------")

if __name__ == "__main__":
    run_bot()
