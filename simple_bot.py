import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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

# Global variables
PARENT_CHAT_ID = None
USER_DATA = {
    "user_near_school": False,
    "user_arrived": False,
    "monitoring_active": False,
    "device_id": None
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command with hardcoded device mapping"""
    global PARENT_CHAT_ID, USER_DATA
    
    chat_id = update.message.chat_id
    keyboard = [["1"]]  # Hanya tampilkan nomor 1
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Tekan tombol 1 untuk mendaftar sebagai orang tua #1:",
        reply_markup=reply_markup
    )

async def handle_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle parent number selection"""
    global PARENT_CHAT_ID, USER_DATA
    
    if update.message.text == "1":
        PARENT_CHAT_ID = update.message.chat_id
        USER_DATA = {
            "user_near_school": False,
            "user_arrived": False,
            "monitoring_active": True,
            "start_time": datetime.now(),
            "device_id": "nino_001"  # Hardcoded untuk nomor 1
        }
        
        await update.message.reply_text(
            "✅ Berhasil terdaftar sebagai orang tua #1\n"
            "📱 Device ID: nino_001\n\n"
            "📍 Untuk mulai monitoring:\n"
            "1. Tekan ikon 📎 (attachment)\n"
            "2. Pilih 'Lokasi'\n"
            "3. Pilih 'Bagikan lokasi terkini'\n"
            "4. Pilih durasi monitoring\n\n"
            "Ketik /status untuk melihat status monitoring",
            reply_markup=ReplyKeyboardRemove()
        )
        logger.info(f"User {PARENT_CHAT_ID} registered with device nino_001")
    else:
        await update.message.reply_text(
            "❌ Input tidak valid. Gunakan /start untuk memilih ulang.",
            reply_markup=ReplyKeyboardRemove()
        )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check monitoring status"""
    global USER_DATA
    
    if USER_DATA.get("monitoring_active", False):
        start_time = USER_DATA.get('start_time', datetime.now()).strftime("%H:%M:%S")
        near_school = "✅ Ya" if USER_DATA.get('user_near_school', False) else "❌ Tidak"
        arrived = "✅ Ya" if USER_DATA.get('user_arrived', False) else "❌ Tidak"
        device_id = USER_DATA.get('device_id', 'Tidak terdaftar')
        
        status_msg = (
            f"📊 Status Monitoring\n\n"
            f"🕐 Dimulai: {start_time}\n"
            f"📍 Dekat sekolah: {near_school}\n"
            f"🚗 Sudah tiba: {arrived}\n"
            f"📱 Device ID: {device_id}\n"
            f"🤖 Status: Aktif"
        )
    else:
        status_msg = "❌ Monitoring tidak aktif. Ketik /start untuk memulai."
    
    await update.message.reply_text(status_msg)

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle location updates"""
    global PARENT_CHAT_ID, USER_DATA
    
    if not update.message or not update.message.location:
        return
    
    if not USER_DATA.get("monitoring_active", False):
        return
    
    user_location = (update.message.location.latitude, update.message.location.longitude)
    distance = geodesic(user_location, SCHOOL_COORDS).km
    
    # Cek apakah sudah dekat sekolah (dalam radius 1km)
    if distance <= RADIUS_KM:
        if not USER_DATA["user_near_school"]:
            await update.message.reply_text(
                f"✅ Anda sudah berada dekat dengan sekolah\n"
                f"📏 Jarak: {distance:.2f} km"
            )
            # Kirim ke Antares (kondisi: posisi_ortu_dekat)
            success = await send_to_antares(USER_DATA["device_id"], kondisi="posisi_ortu_dekat")
            if success:
                logger.info("send_to_antares succeeded")
            else:
                logger.warning("send_to_antares failed")
            USER_DATA["user_near_school"] = True
        
        # Cek apakah sudah sampai di sekolah (dalam radius 0.1km)
        if distance <= ARRIVAL_RADIUS_KM and not USER_DATA["user_arrived"]:
            keyboard = [["Ya", "Tidak"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
            await update.message.reply_text(
                "🚸 Apakah Anda sudah menjemput anak Anda?",
                reply_markup=reply_markup
            )
            USER_DATA["user_arrived"] = True
    else:
        USER_DATA["user_near_school"] = False

async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle yes/no responses"""
    global PARENT_CHAT_ID, USER_DATA
    
    text = update.message.text.lower()
    
    if text == "ya":
        USER_DATA["monitoring_active"] = False
        USER_DATA["user_arrived"] = False
        await update.message.reply_text(
            "🔕 Monitoring dihentikan.\n\n"
            "🛡️ Hati-hati di jalan!\n\n"
            "💡 Ketik /start besok untuk monitoring lagi.",
            reply_markup=ReplyKeyboardRemove()
        )
    elif text == "tidak":
        await update.message.reply_text(
            "🔔 Monitoring dilanjutkan!",
            reply_markup=ReplyKeyboardRemove()
        )

async def send_to_antares(device_id: str, kondisi: str = "posisi_ortu_dekat"):
    """Send data to Antares"""
    # Validate configuration
    if not ANTARES_URL_POST:
        logger.error("ANTARES_URL_POST is not set. Cannot send to Antares.")
        return False
    if not ANTARES_ACCESS_KEY:
        logger.error("ANTARES_ACCESS_KEY is not set. Cannot send to Antares.")
        return False

    url = f"{ANTARES_URL_POST.rstrip('/')}"

    # Build payload to match Java string:
    # {"m2m:cin": {"con": "{\"kondisi\":\"<kondisi>\",\"device_id\":\"<device_id>\"}"}}
    inner_con = json.dumps({"kondisi": kondisi, "device_id": device_id})
    payload = {
        "m2m:cin": {
            "con": inner_con
        }
    }

    headers = {
        "X-M2M-Origin": ANTARES_ACCESS_KEY,
        "Content-Type": "application/json;ty=4",
        "Accept": "application/json"
    }

    logger.info(f"Sending to Antares URL={url}")
    logger.debug(f"Headers: {headers}")
    logger.debug(f"Payload: {json.dumps(payload)}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                text = await response.text()
                logger.info(f"Antares response status={response.status}")
                logger.debug(f"Antares response body={text}")
                if response.status in (200, 201, 202):
                    logger.info(f"Data berhasil dikirim ke Antares untuk device {device_id}")
                    return True
                else:
                    logger.error(f"Gagal kirim ke Antares: status={response.status}")
                    return False
    except Exception as e:
        logger.error(f"Error kirim ke Antares: {e}")
        return False

async def handle_antares_webhook(request):
    """Handle webhook from Antares"""
    global PARENT_CHAT_ID, USER_DATA
    
    try:
        data = await request.json()
        logger.info(f"Data dari Antares: {json.dumps(data)}")
        
        if not PARENT_CHAT_ID or not USER_DATA.get("monitoring_active"):
            return web.json_response({"status": "not_monitoring"})
        
        # Parse kondisi jatuh
        kondisi = None
        if "m2m:cin" in data:
            try:
                content = json.loads(data["m2m:cin"]["con"])
                kondisi = content.get("kondisi")
            except:
                pass
        
        if kondisi == "terjatuh":
            bot_app = request.app["bot_app"]
            timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            
            message = (
                "🚨 **ALERT DARURAT** 🚨\n\n"
                "⚠️ ANAK ANDA TERJATUH!\n"
                f"🕐 Waktu: {timestamp}\n"
                "📍 Segera cek lokasi anak!\n\n"
                "🚨 Mohon segera ambil tindakan!"
            )
            
            await bot_app.bot.send_message(
                chat_id=PARENT_CHAT_ID,
                text=message,
                parse_mode='Markdown'
            )
            return web.json_response({"status": "alert_sent"})
        
        return web.json_response({"status": "no_alert_needed"})
    
    except Exception as e:
        logger.error(f"Error webhook: {e}")
        return web.json_response({"status": "error"}, status=500)

async def init_app():
    """Initialize application"""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN tidak ditemukan!")
        return None
    
    # Setup bot
    app = Application.builder().token(TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.Regex("^[1-6]$"), handle_number))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Regex("^(Ya|Tidak|ya|tidak)$"), handle_response))
    
    # Initialize bot
    await app.initialize()
    
    # Setup web app
    web_app = web.Application()
    web_app["bot_app"] = app
    
    # Register routes
    web_app.add_routes([
        web.post("/monitor", handle_antares_webhook),
        web.post("/monitor/{device_id}", handle_antares_webhook)
    ])
    
    return app, web_app

def main():
    """Main function"""
    async def run_server():
        result = await init_app()
        if result is None:
            return
        
        bot_app, web_app = result
        
        # Start bot
        await bot_app.start()
        await bot_app.updater.start_polling()
        logger.info("🤖 Bot started")
        
        # Run web server
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=WEBHOOK_PORT)
        await site.start()
        
        logger.info(f"🚀 Server running on port {WEBHOOK_PORT}")
        
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await bot_app.updater.stop()
            await bot_app.stop()
            await runner.cleanup()
    
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()