import os
import logging
import asyncio
from datetime import datetime
import anthropic
import openai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)
import gspread
from google.oauth2.service_account import Credentials
import json
import tempfile

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOSS_CHAT_ID = int(os.environ.get("BOSS_CHAT_ID", "0"))
ANNOUNCEMENT_GROUP_ID = os.environ.get("ANNOUNCEMENT_GROUP_ID", "")
GOOGLE_SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

# Clients
claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

# In-memory storage
conversations = {}  # chat_id -> list of messages
clients_db = {}     # chat_id -> client info
current_prices = {} # product -> price
announcement_job = None

# =====================
# NARGIZA SYSTEM PROMPT
# =====================
SYSTEM_PROMPT = """Sen Nargiza - Petro Plast kompaniyasining professional savdo agentisan.

MUHIM QOIDALAR:
1. Doim o'zbek yoki rus tilida gapir (mijoz qaysi tilda yozsa, o'sha tilda javob ber)
2. Professional, samimiy va ishonchli bo'l
3. Hech qachon bot ekanligingni o'zing aytma (so'rashsa: "Men Nargiza, savdo menedjeri" de)
4. Har bir javobdan keyin mijozni sotuvga bir qadam yaqinlashtir
5. Hech qachon o'ylab ma'lumot berma - bilmasang boss dan so'ra

MAHSULOTLAR:
- HDPE (yuqori zichlikli polietilen) - qattiq qoplar, quvurlar uchun
- LDPE (past zichlikli polietilen) - yumshoq paketlar uchun  
- LLDPE (chiziqli past zichlikli polietilen) - stretch plyonka uchun
- PP (polipropilen) - barcha markalar
- PVC - qattiq va yumshoq mahsulotlar uchun
- Ikkilamchi polietilen - arzon variant
- ABC markalar - rangli granulalar

NARXLAR: {prices}

AFZALLIKLARIMIZ:
- Haftada 1 kun Toshkent ichida TEKIN yetkazish
- 25 kg dan buyurtma qabul qilinadi
- Sertifikat va texnik xarakteristika beriladi
- Tezkor javob - 24/7

SAVDO JARAYONI:
1. Yangi mijoz yozsa - salom de, ismini so'ra, ehtiyojini bilib ol
2. Mahsulot va narx so'rasa - mavjud narxlardan ayt
3. Mijoz qiziqsa - miqdorini, to'lov turini so'ra
4. ISSIQ LID bo'lsa - boss ga DARHOL xabar yubor:
   "🔥 ISSIQ LID: [ism], [mahsulot], [miqdor], [narx], [to'lov], [raqam]"
5. Narx o'zgarmadimi deb so'rashim kerak bo'lsa - "Bir daqiqa" de va boss dan so'ra

MIJOZ KATEGORIYALARI:
- 🆕 Yangi mijoz - birinchi marta yozgan
- ❄️ Sovuq lid - narx so'rab ketgan
- 🔥 Issiq lid - olishga tayyor
- ✅ Bir martalik - 1 marta olgan
- 💎 Doimiy mijoz - 2+ marta olgan

E'TIROZ BILAN ISHLASH:
- "Boshqada arzon" -> tekin yetkazishni eslatish, sifatni aytish
- "O'ylab ko'raman" -> narx o'zgarishi mumkinligini aytish
- "Keyinroq" -> 2-3 kunda eslatma yuborish

MUHIM: Suhbatni hech qachon ochiq qoldirma. Har doim savol ber yoki taklif qil!"""


def get_prices_text():
    if not current_prices:
        return "Bugungi narxlar hali kiritilmagan"
    lines = ["Bugungi narxlar:"]
    for product, price in current_prices.items():
        lines.append(f"• {product}: {price:,} so'm/kg")
    return "\n".join(lines)


def get_system_prompt():
    return SYSTEM_PROMPT.format(prices=get_prices_text())


# =====================
# GOOGLE SHEETS
# =====================
def get_sheets_client():
    if not GOOGLE_CREDENTIALS_JSON:
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://spreadsheets.google.com/feeds", 
                    "https://www.googleapis.com/auth/drive"]
        )
        return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"Sheets error: {e}")
        return None


def save_client_to_sheets(client_info):
    try:
        gc = get_sheets_client()
        if not gc or not GOOGLE_SHEETS_ID:
            return
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)
        worksheet = sh.worksheet("Mijozlar")
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            client_info.get("name", ""),
            client_info.get("phone", ""),
            client_info.get("telegram", ""),
            client_info.get("category", "🆕 Yangi"),
            client_info.get("product", ""),
            client_info.get("quantity", ""),
            client_info.get("notes", ""),
        ]
        worksheet.append_row(row)
    except Exception as e:
        logger.error(f"Save to sheets error: {e}")


def save_sale_to_sheets(sale_info):
    try:
        gc = get_sheets_client()
        if not gc or not GOOGLE_SHEETS_ID:
            return
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)
        worksheet = sh.worksheet("Sotuvlar")
        row = [
            datetime.now().strftime("%Y-%m-%d"),
            sale_info.get("client", ""),
            sale_info.get("product", ""),
            sale_info.get("quantity", ""),
            sale_info.get("price", ""),
            sale_info.get("total", ""),
            sale_info.get("payment", ""),
        ]
        worksheet.append_row(row)
    except Exception as e:
        logger.error(f"Save sale error: {e}")


# =====================
# AI FUNCTIONS
# =====================
async def get_nargiza_response(chat_id: int, user_message: str) -> str:
    if chat_id not in conversations:
        conversations[chat_id] = []
    
    conversations[chat_id].append({
        "role": "user",
        "content": user_message
    })
    
    # Keep last 20 messages
    if len(conversations[chat_id]) > 20:
        conversations[chat_id] = conversations[chat_id][-20:]
    
    try:
        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=get_system_prompt(),
            messages=conversations[chat_id]
        )
        
        assistant_message = response.content[0].text
        conversations[chat_id].append({
            "role": "assistant",
            "content": assistant_message
        })
        
        return assistant_message
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return "Kechirasiz, texnik muammo. Qaytadan yozing."


async def transcribe_voice(file_path: str) -> str:
    try:
        with open(file_path, "rb") as audio_file:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="uz"
            )
        return transcript.text
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return ""


# =====================
# BOSS FUNCTIONS
# =====================
def is_boss(chat_id: int) -> bool:
    return chat_id == BOSS_CHAT_ID


async def notify_boss(context: ContextTypes.DEFAULT_TYPE, message: str):
    if BOSS_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=BOSS_CHAT_ID,
                text=message,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Notify boss error: {e}")


async def parse_boss_prices(text: str) -> dict:
    """Parse prices from boss message"""
    prices = {}
    lines = text.strip().split('\n')
    for line in lines:
        if '-' in line or ':' in line:
            separator = '-' if '-' in line else ':'
            parts = line.split(separator, 1)
            if len(parts) == 2:
                product = parts[0].strip()
                price_str = parts[1].strip().replace(' ', '').replace(',', '').replace("so'm", '').replace('sum', '')
                try:
                    price = int(''.join(filter(str.isdigit, price_str)))
                    if price > 0:
                        prices[product] = price
                except:
                    pass
    return prices


# =====================
# ANNOUNCEMENT
# =====================
async def send_announcement(context: ContextTypes.DEFAULT_TYPE):
    if not ANNOUNCEMENT_GROUP_ID or not current_prices:
        return
    
    prices_text = get_prices_text()
    message = f"""🏭 <b>PETRO PLAST — Polimer Xomashyosi</b>

{prices_text}

📦 <b>Assortiment:</b>
• HDPE, LDPE, LLDPE
• PP — barcha markalar
• PVC, Ikkilamchi polietilen
• 25 kg dan buyurtma

🚚 <b>Toshkent ichida yetkazish</b> — haftada 1 kun TEKIN!
💬 Buyurtma: @Nargiza_petroplast_bot

⏰ {datetime.now().strftime("%H:%M")}"""

    try:
        await context.bot.send_message(
            chat_id=ANNOUNCEMENT_GROUP_ID,
            text=message,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Announcement error: {e}")


# =====================
# HANDLERS
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if is_boss(chat_id):
        await update.message.reply_text(
            "👋 Salom Boss!\n\n"
            "Buyruqlar:\n"
            "/narx - Bugungi narxlarni yuborish\n"
            "/hisobot - Kunlik hisobot\n"
            "/mijozlar - Mijozlar ro'yxati\n"
            "/elon_boshlash - Guruhga e'lon yuborishni boshlash\n"
            "/elon_toxtatish - E'lonni to'xtatish\n"
            "/yordam - Barcha buyruqlar"
        )
    else:
        response = await get_nargiza_response(
            chat_id,
            "Salom, men birinchi marta yozayapman"
        )
        await update.message.reply_text(response)
        
        # Save new client
        clients_db[chat_id] = {
            "name": update.effective_user.first_name or "",
            "telegram": f"@{update.effective_user.username}" if update.effective_user.username else "",
            "category": "🆕 Yangi",
            "first_contact": datetime.now().strftime("%Y-%m-%d %H:%M")
        }


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    # BOSS commands
    if is_boss(chat_id):
        await handle_boss_message(update, context, text)
        return
    
    # Client message
    response = await get_nargiza_response(chat_id, text)
    await update.message.reply_text(response)
    
    # Check if hot lead
    if any(word in response.lower() for word in ["🔥 issiq lid", "boss ga", "hozir qiling"]):
        client = clients_db.get(chat_id, {})
        await notify_boss(
            context,
            f"🔥 <b>ISSIQ LID!</b>\n\n"
            f"👤 Ism: {client.get('name', 'Noma\'lum')}\n"
            f"📱 Telegram: {client.get('telegram', '')}\n"
            f"💬 So'nggi xabar: {text}\n\n"
            f"<b>Nargiza javobi:</b>\n{response}"
        )


async def handle_boss_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    chat_id = update.effective_chat.id
    
    # Check if prices
    if any(keyword in text.lower() for keyword in ['hdpe', 'ldpe', 'pp', 'pvc', 'lldpe', 'narx']):
        prices = await parse_boss_prices(text)
        if prices:
            current_prices.update(prices)
            prices_text = "\n".join([f"✅ {k}: {v:,} so'm" for k, v in prices.items()])
            await update.message.reply_text(
                f"✅ Narxlar yangilandi!\n\n{prices_text}\n\n"
                f"Nargiza endi bu narxlar bilan ishlaydi."
            )
            return
    
    # Check if sale confirmation
    if text.lower().startswith("oldi") or "sotildi" in text.lower():
        await update.message.reply_text("✅ Yaxshi! Sotuv qayd etildi.")
        return
    
    # Regular boss message - treat as instruction to Nargiza
    await update.message.reply_text(
        f"✅ Qabul qildim!\n{text}"
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    await update.message.reply_text("🎤 Ovozingizni eshitdim, bir daqiqa...")
    
    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            text = await transcribe_voice(tmp.name)
        
        if text:
            response = await get_nargiza_response(chat_id, text)
            await update.message.reply_text(f"🎤 <i>Siz aytdingiz: {text}</i>\n\n{response}", parse_mode="HTML")
        else:
            await update.message.reply_text("Ovozingizni tushunmadim, matn yozing.")
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Ovozni qayta ishlashda xato. Matn yozing.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if is_boss(chat_id):
        # Boss sent photo - save for forwarding
        context.bot_data['last_boss_photo'] = update.message.photo[-1].file_id
        await update.message.reply_text("✅ Rasm saqlandi. Qaysi mijozga yuboray?")
        return
    
    # Client sent photo
    response = await get_nargiza_response(
        chat_id,
        "Mijoz menga rasm yubordi"
    )
    await update.message.reply_text(response)
    
    # Forward to boss
    await notify_boss(
        context,
        f"📸 Mijoz rasm yubordi!\n"
        f"Chat ID: {chat_id}\n"
        f"Ism: {update.effective_user.first_name}"
    )
    await context.bot.forward_message(
        chat_id=BOSS_CHAT_ID,
        from_chat_id=chat_id,
        message_id=update.message.message_id
    )


# =====================
# BOSS COMMANDS
# =====================
async def cmd_narx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    
    await update.message.reply_text(
        "Bugungi narxlarni yuboring. Misol:\n\n"
        "HDPE 1561 - 16700\n"
        "LDPE 158 - 15200\n"
        "PP 01030 - 18500\n\n"
        "Har bir mahsulot alohida qatorda."
    )


async def cmd_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    
    total_clients = len(clients_db)
    categories = {}
    for client in clients_db.values():
        cat = client.get('category', 'Noma\'lum')
        categories[cat] = categories.get(cat, 0) + 1
    
    report = f"📊 <b>KUNLIK HISOBOT</b>\n"
    report += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    report += f"👥 Jami mijozlar: {total_clients}\n\n"
    
    for cat, count in categories.items():
        report += f"{cat}: {count} ta\n"
    
    if current_prices:
        report += f"\n💰 <b>Bugungi narxlar:</b>\n"
        for product, price in current_prices.items():
            report += f"• {product}: {price:,} so'm\n"
    
    await update.message.reply_text(report, parse_mode="HTML")


async def cmd_elon_boshlash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    
    if not current_prices:
        await update.message.reply_text("❌ Avval narxlarni kiriting!")
        return
    
    # Schedule announcements every hour
    job_queue = context.job_queue
    job_queue.run_repeating(
        send_announcement,
        interval=3600,
        first=10,
        name="announcement"
    )
    
    await update.message.reply_text(
        "✅ E'lonlar boshlandi!\n"
        "Har soatda guruhga yuboriladi.\n"
        "To'xtatish: /elon_toxtatish"
    )


async def cmd_elon_toxtatish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    
    current_jobs = context.job_queue.get_jobs_by_name("announcement")
    for job in current_jobs:
        job.schedule_removal()
    
    await update.message.reply_text("⛔ E'lonlar to'xtatildi.")


async def cmd_mijozlar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    
    if not clients_db:
        await update.message.reply_text("Hali mijozlar yo'q.")
        return
    
    text = "👥 <b>MIJOZLAR RO'YXATI</b>\n\n"
    for chat_id, client in list(clients_db.items())[-10:]:  # Last 10
        text += f"• {client.get('name', 'Noma\'lum')} - {client.get('category', '')}\n"
    
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    
    await update.message.reply_text(
        "📋 <b>BARCHA BUYRUQLAR</b>\n\n"
        "/narx - Narxlarni yangilash\n"
        "/hisobot - Kunlik hisobot\n"
        "/mijozlar - Mijozlar ro'yxati\n"
        "/elon_boshlash - Guruhga e'lonni boshlash\n"
        "/elon_toxtatish - E'lonni to'xtatish\n\n"
        "💡 <b>Oddiy xabar yuboring:</b>\n"
        "• Narxlar uchun: HDPE 1561 - 16700\n"
        "• Sotuv uchun: 'Oldi' yoki 'Sotildi'\n",
        parse_mode="HTML"
    )


# =====================
# MAIN
# =====================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("narx", cmd_narx))
    app.add_handler(CommandHandler("hisobot", cmd_hisobot))
    app.add_handler(CommandHandler("mijozlar", cmd_mijozlar))
    app.add_handler(CommandHandler("elon_boshlash", cmd_elon_boshlash))
    app.add_handler(CommandHandler("elon_toxtatish", cmd_elon_toxtatish))
    app.add_handler(CommandHandler("yordam", cmd_yordam))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logger.info("Nargiza bot ishga tushdi! 🚀")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
