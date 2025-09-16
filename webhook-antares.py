import os
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from geopy.distance import geodesic
from aiohttp import web
import logging
import aiohttp

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SCHOOL_COORDS = tuple(map(float, os.getenv("SCHOOL_COORDS", "0,0").split(",")))
RADIUS_KM = float(os.getenv("RADIUS_KM", "1.0"))
ARRIVAL_RADIUS_KM = float(os.getenv("ARRIVAL_RADIUS_KM", "0.1"))
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
ANTARES_URL_POST = os.getenv("ANTARES_URL_POST")
ANTARES_ACCESS_KEY = os.getenv("ANTARES_ACCESS_KEY")

# Global variables untuk single parent
PARENT_CHAT_ID = None
USER_DATA = {
    "user_near_school": False,
    "user_arrived": False,
    "monitoring_active": False
}

# Class untuk mengirim pesan ke Telegram
class TelegramMessageSender:
    def __init__(self, bot_app: Application):
        self.bot = bot_app.bot
    
    # Alert ketika anak terjatuh
    async def send_fall_alert(self, chat_id: int):
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        message = (
            "🚨 **ALERT DARURAT** 🚨\n\n"
            "⚠️ ANAK ANDA TERJATUH!\n"
            f"🕐 Waktu: {timestamp}\n"
            "📍 Segera cek lokasi anak dan hubungi sekolah!\n\n"
            "🚨 Mohon segera ambil tindakan!"
        )
        
        try:
            await self.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
            logger.info(f"Fall alert sent to {chat_id} at {timestamp}")
        except Exception as e:
            logger.error(f"Failed to send fall alert: {e}")
    
    # Kirim pesan ketika orang tua sudah dekat sekolah
    async def send_location_near_school(self, chat_id: int, distance: float):
        message = f"✅ Anda sudah berada dekat dengan sekolah\n📏 Jarak: {distance:.2f} km"
        
        try:
            await self.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Near school notification sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send location update: {e}")
    
    # Kirim pesan apakah anak sudah dijemput
    async def send_pickup_prompt(self, chat_id: int):
        keyboard = [["Ya", "Tidak"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        try:
            await self.bot.send_message(
                chat_id=chat_id, 
                text="🚸 Apakah Anda sudah menjemput anak Anda?", 
                reply_markup=reply_markup
            )
            logger.info(f"Pickup prompt sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send pickup prompt: {e}")
    
    # Kirim pesan ketika monitoring dihentikan
    async def send_monitoring_stopped(self, chat_id: int):
        message = (
            "🔕 Monitoring dihentikan.\n\n"
            "🛡️ Hati-hati di jalan dan semoga sampai tujuan dengan selamat!\n\n"
            "💡 Jangan lupa untuk mengetik /start lagi di esok hari agar "
            "bot ChildMonitoring berjalan kembali."
        )
        
        try:
            await self.bot.send_message(
                chat_id=chat_id, 
                text=message, 
                reply_markup=ReplyKeyboardRemove()
            )
            logger.info(f"Monitoring stopped message sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send monitoring stopped message: {e}")
    
    # Kirim pesan ketika monitoring dilanjutkan
    async def send_monitoring_continued(self, chat_id: int):
        keyboard = [["Ya", "Tidak"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text="🔔 Monitoring dilanjutkan!\n\n🚸 Apakah Anda sudah menjemput anak Anda?",
                reply_markup=reply_markup
            )
            logger.info(f"Monitoring continued message sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send monitoring continued message: {e}")
    
    # Kirim data ke Antares ketika ortu sudah dekat
    async def send_to_antares(self, data: dict):
        url = f"{ANTARES_URL_POST}" 
        
        payload = {
            "m2m:cin": {
                "xmlns:m2m": "http://www.onem2m.org/xml/protocols",  
                "cnf": "application/json",
                "con": "{\"posisi_ortu_dekat\":\"ya\"}"
            }
        }
        
        headers = {
            "X-M2M-Origin": ANTARES_ACCESS_KEY,
            "Content-Type": "application/json;ty=4",
            "Accept": "application/json"
        }

        logger.info(f"Sending to URL: {url}")
        logger.info(f"Headers: {headers}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 201:
                        logger.info("Data berhasil dikirim ke Antares")
                    else:
                        logger.error(f"Gagal kirim ke Antares: {response.status}")
        except Exception as e:
            logger.error(f"Error kirim ke Antares: {e}")


# Handler untuk command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PARENT_CHAT_ID, USER_DATA
    
    PARENT_CHAT_ID = update.message.chat_id
    USER_DATA = {
        "user_near_school": False,
        "user_arrived": False,
        "monitoring_active": True,
        "start_time": datetime.now()
    }
    
    context.user_data.clear()
    
    tutorial = (
        "🤖 **Child Monitoring Bot Aktif!**\n\n"
        "📍 **Untuk menyalakan Live Location:**\n"
        "1. Tekan ikon 📎 (attachment) di kolom chat.\n"
        "2. Pilih 'Lokasi'.\n"
        "3. Pilih 'Bagikan lokasi terkini' (Live Location).\n"
        "4. Pilih durasi (15 menit, 1 jam, atau 8 jam).\n\n"
        "✅ Bot akan memantau posisi Anda sampai sekolah\n"
        "🚨 Bot akan mengirim alert jika anak terjatuh\n\n"
        "Ketik /status untuk melihat status monitoring"
    )
    
    await update.message.reply_text(tutorial, parse_mode='Markdown')
    logger.info(f"Monitoring started for user {PARENT_CHAT_ID}")


# Handler untuk command /status
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global USER_DATA
    
    if USER_DATA.get("monitoring_active", False):
        start_time = USER_DATA.get('start_time', datetime.now()).strftime("%H:%M:%S")
        near_school = "✅ Ya" if USER_DATA.get('user_near_school', False) else "❌ Tidak"
        arrived = "✅ Ya" if USER_DATA.get('user_arrived', False) else "❌ Tidak"
        
        status_msg = (
            f"📊 **Status Monitoring**\n\n"
            f"🕐 Dimulai: {start_time}\n"
            f"📍 Dekat sekolah: {near_school}\n"
            f"🚗 Sudah tiba: {arrived}\n"
            f"🤖 Status: Aktif"
        )
    else:
        status_msg = "❌ Monitoring tidak aktif. Ketik /start untuk memulai."
    
    await update.message.reply_text(status_msg, parse_mode='Markdown')

# Handler untuk lokasi yang dikirim user
async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PARENT_CHAT_ID, USER_DATA
    
    if not update.message or not update.message.location:
        return
    
    if not USER_DATA.get("monitoring_active", False):
        return
    
    user_location = (update.message.location.latitude, update.message.location.longitude)
    distance = geodesic(user_location, SCHOOL_COORDS).km
    
    message_sender = TelegramMessageSender(context.application)
    
    # Cek apakah sudah dekat sekolah (dalam radius 1km)
    if distance <= RADIUS_KM:
        if not USER_DATA["user_near_school"]:
            await message_sender.send_location_near_school(PARENT_CHAT_ID, distance)

            # Mengirim data ke Antares
            await message_sender.send_to_antares({"posisi_ortu_dekat": "ya"})
            USER_DATA["user_near_school"] = True
        
        # Cek apakah sudah sampai di sekolah (dalam radius 0.1km)
        if distance <= ARRIVAL_RADIUS_KM and not USER_DATA["user_arrived"]:
            await message_sender.send_pickup_prompt(PARENT_CHAT_ID)
            USER_DATA["user_arrived"] = True
    else:
        USER_DATA["user_near_school"] = False


# Handler untuk respons pengguna apakah anak sudah dijemput (Ya/Tidak)
async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PARENT_CHAT_ID, USER_DATA
    
    text = update.message.text.lower()
    message_sender = TelegramMessageSender(context.application)
    
    if text == "ya":
        await message_sender.send_monitoring_stopped(PARENT_CHAT_ID)
        USER_DATA["user_arrived"] = False
        USER_DATA["monitoring_active"] = False
        
        logger.info(f"User {PARENT_CHAT_ID} completed pickup")
    
    elif text == "tidak":
        await message_sender.send_monitoring_continued(PARENT_CHAT_ID)


# Webhook handler untuk data dari Antares (deteksi jatuh)
async def handle_antares_webhook(request):
    global PARENT_CHAT_ID, USER_DATA
    
    try:
        data = await request.json()
        logger.info(f"📡 Data dari Antares: {json.dumps(data, indent=2)}")
        
        # Cek apakah monitoring aktif dan ada parent yang terdaftar
        if not PARENT_CHAT_ID:
            logger.warning("No parent registered yet - waiting for /start command")
            return web.json_response({"status": "no_parent_registered", "message": "Please send /start to bot first"})
        
        if not USER_DATA.get("monitoring_active", False):
            logger.warning("Monitoring not active - parent needs to send /start")
            return web.json_response({"status": "monitoring_inactive", "message": "Please send /start to activate monitoring"})
        
        # Ambil bot application dari web app
        bot_app: Application = request.app["bot_app"]
        message_sender = TelegramMessageSender(bot_app)
        
        kondisi = None
        
        # Handle format m2m:sgn (subscription/notification)
        if "m2m:sgn" in data:
            sgn_data = data["m2m:sgn"]
            logger.info(f"Received m2m:sgn notification: {sgn_data}")
            
            # Jika ini hanya notifikasi subscription, return OK
            if sgn_data.get("m2m:vrq") is True:
                logger.info("Subscription verification received")
                return web.json_response({"status": "subscription_verified"})
            
            # Handle m2m:nev (notification event) dengan m2m:cin di dalamnya
            if "m2m:nev" in sgn_data and "m2m:rep" in sgn_data["m2m:nev"]:
                cin_data = sgn_data["m2m:nev"]["m2m:rep"].get("m2m:cin")
                if cin_data and "con" in cin_data:
                    try:
                        content = json.loads(cin_data["con"])
                        kondisi = content.get("kondisi")
                        logger.info(f"Parsed kondisi from m2m:nev: {kondisi}")
                    except json.JSONDecodeError:
                        logger.error("Failed to parse content from m2m:nev")
        
        # Handle format m2m:cin (content instance) 
        elif "m2m:cin" in data:
            try:
                content = json.loads(data["m2m:cin"]["con"])
                kondisi = content.get("kondisi")
                logger.info(f"Parsed kondisi from m2m:cin: {kondisi}")
            except json.JSONDecodeError:
                logger.error("Failed to parse Antares content")
                return web.json_response({"status": "invalid_json"})
        
        # Handle direct format
        elif "kondisi" in data:
            kondisi = data.get("kondisi")
            logger.info(f"Direct kondisi received: {kondisi}")
        
        else:
            logger.warning(f"Unknown data format from Antares: {list(data.keys())}")
            return web.json_response({"status": "unknown_format", "received_keys": list(data.keys())})
        
        # Handle jika kondisi adalah "terjatuh"
        if kondisi == "terjatuh":
            await message_sender.send_fall_alert(PARENT_CHAT_ID)
            logger.info(f"Fall alert sent to parent {PARENT_CHAT_ID}")
            return web.json_response({"status": "alert_sent", "condition": kondisi})
        
        else:
            logger.info(f"Received condition '{kondisi}' but only 'terjatuh' is processed")
            return web.json_response({"status": "condition_ignored", "condition": kondisi})
    
    except Exception as e:
        logger.error(f"❌ Error webhook: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


# Health check
async def health_check(request):
    global PARENT_CHAT_ID, USER_DATA
    
    return web.json_response({
        "status": "healthy",
        "has_active_parent": PARENT_CHAT_ID is not None,
        "monitoring_active": USER_DATA.get("monitoring_active", False),
        "timestamp": datetime.now().isoformat()
    })


async def init_app():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN tidak ditemukan!")
        return None
    
    if not SCHOOL_COORDS or SCHOOL_COORDS == (0, 0):
        logger.error("COFFEE_COORDS tidak valid!")
        return None
    
    logger.info(f"Initializing bot with token: {TOKEN[:10]}...")
    
    # Setup Telegram bot
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Regex("^(Ya|Tidak|ya|tidak)$"), handle_response))
    
    # Initialize bot
    await app.initialize()
    
    # Setup web application untuk webhook
    web_app = web.Application()
    web_app["bot_app"] = app
    
    # Register routes
    web_app.add_routes([
        web.post("/monitor", handle_antares_webhook),
        web.get("/health", health_check)
    ])
    
    logger.info(f"🤖 Bot & Webhook starting on port {WEBHOOK_PORT}")
    logger.info(f"📍 School coordinates: {SCHOOL_COORDS}")
    logger.info(f"📏 Monitoring radius: {RADIUS_KM} km")
    logger.info(f"🚨 Monitoring for 'terjatuh' condition only")
    
    return app, web_app

def main():
    async def run_server():
        result = await init_app()
        if result is None:
            return
        
        bot_app, web_app = result
        
        # Start bot polling
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info("🤖 Bot polling started")
        
        # Run web server
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=WEBHOOK_PORT)
        await site.start()
        
        logger.info(f"🚀 Server running on http://0.0.0.0:{WEBHOOK_PORT}")
        logger.info("✅ Bot and webhook ready! Press Ctrl+C to stop")
        
        # Keep running both services
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        finally:
            await bot_app.updater.stop()
            await bot_app.stop()
            await runner.cleanup()
            logger.info("✅ Cleanup complete")
    
    # Run the server
    try:    
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Program terminated by user")
    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == "__main__":
    main()