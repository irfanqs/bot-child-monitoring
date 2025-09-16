import os
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from geopy.distance import geodesic

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

SCHOOL_COORDS = os.getenv("COFFEE_COORDS")

RADIUS_KM = 1.0
ARRIVAL_RADIUS_KM = 0.1

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    
    tutorial = (
        "📍 Untuk menyalakan Live Location:\n"
        "1. Tekan ikon 📎 (attachment) di kolom chat.\n"
        "2. Pilih 'Lokasi'.\n"
        "3. Pilih 'Bagikan lokasi terkini' (Live Location).\n"
        "4. Pilih durasi (15 menit, 1 jam, atau 8 jam).\n\n"
        "Dengan begitu saya bisa memantau posisi Anda sampai sekolah."
    )
    await update.message.reply_text(tutorial)

# Handler lokasi
async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.location:
      return
     
    user_location = (update.message.location.latitude, update.message.location.longitude)
    distance = geodesic(user_location, SCHOOL_COORDS).km

    # Jika pengguna berada dalam radius 1 km dari sekolah
    if distance <= 1:
        if not context.user_data.get("user_near_school", False):
            await update.message.reply_text("✅ Anda sudah berada dekat dengan sekolah (≤1 km).")
            context.user_data["user_near_school"] = True
        
        # Jika sudah benar-benar sampai (≤100m)
        if distance <= ARRIVAL_RADIUS_KM and not context.user_data.get("user_arrived", False):
            keyboard = [["Ya", "Tidak"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
            await update.message.reply_text("🚸 Apakah Anda sudah menjemput anak Anda?", reply_markup=reply_markup)
            context.user_data["user_arrived"] = True

    else:
        context.user_data["user_near_school"] = False

# Handler respons pengguna ketika anak sudah dijemput atau belum
async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()

    if text == "ya":
        await update.message.reply_text(
            "🔕 Buzzer berhenti.",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text(
            "🛡️ Hati-hati di jalan dan semoga sampai tujuan dengan selamat ya!\n\n"
            "Jangan lupa untuk mengetik /start lagi di esok hari agar bot ChildMonitoring berjalan kembali."
        )
        context.user_data["user_arrived"] = False

    elif text == "tidak":
        await update.message.reply_text("🔔 Buzzer dinyalakan kembali!")

        keyboard = [["Ya", "Tidak"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "🚸 Apakah Anda sudah menjemput anak Anda?",
            reply_markup=reply_markup
        )

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Regex("^(Ya|Tidak|ya|tidak)$"), handle_response))

    print("🤖 Bot jalan... tekan Ctrl+C untuk stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
