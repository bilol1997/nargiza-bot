import os
import logging
import asyncio
from datetime import datetime
import anthropic
import openai
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import tempfile

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOSS_CHAT_ID = int(os.environ.get("BOSS_CHAT_ID", "0"))

claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

conversations = {}
clients_db = {}
current_prices = {}

SYSTEM_PROMPT = """Sen Nargiza - Petro Plast kompaniyasining professional savdo agentisan.

QOIDALAR:
1. O'zbek yoki rus tilida gapir (mijoz qaysi tilda yozsa o'sha tilda)
2. Professional va samimiy bo'l
3. Bot ekanligingni aytma
4. Har javobdan keyin sotuvga yaqinlashtir

MAHSULOTLAR: HDPE, LDPE, LLDPE, PP, PVC, Ikkilamchi polietilen

NARXLAR: {prices}

AFZALLIKLAR:
- Haftada 1 kun Toshkent ichida TEKIN yetkazish
- 25 kg dan buyurtma

SAVDO:
1. Yangi mijoz - ismini so'ra, ehtiyojini bil
2. Narx so'rasa - narxlardan ayt
3. Qiziqsa - miqdor, to'lovni so'ra
4. Issiq lid bo'lsa: "ISSIQ LID: [ism], [mahsulot], [miqdor], [narx], [raqam]" yoz"""


def get_prices_text():
    if not current_prices:
        return "Bugungi narxlar kiritilmagan"
    return "\n".join([f"• {p}: {v:,} so'm/kg" for p, v in current_prices.items()])


async def get_nargiza_response(chat_id, user_message):
    if chat_id not in conversations:
        conversations[chat_id] = []
    conversations[chat_id].append({"role": "user", "content": user_message})
    if len(conversations[chat_id]) > 20:
        conversations[chat_id] = conversations[chat_id][-20:]
    try:
        response = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            system=SYSTEM_PROMPT.format(prices=get_prices_text()),
            messages=conversations[chat_id]
        )
        msg = response.content[0].text
        conversations[chat_id].append({"role": "assistant", "content": msg})
        return msg
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return "Kechirasiz, xato. Qaytadan yozing."


def is_boss(chat_id):
    return chat_id == BOSS_CHAT_ID


async def notify_boss(context, message):
    if BOSS_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=BOSS_CHAT_ID, text=message)
        except Exception as e:
            logger.error(f"Boss notify error: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_boss(chat_id):
        await update.message.reply_text("Salom Boss!\n/narx - narx kiritish\n/hisobot - hisobot\n/yordam - yordam")
    else:
        response = await get_nargiza_response(chat_id, "Salom birinchi marta yozayapman")
        await update.message.reply_text(response)
        clients_db[chat_id] = {
            "name": update.effective_user.first_name or "",
            "telegram": f"@{update.effective_user.username}" if update.effective_user.username else "",
            "category": "Yangi"
        }


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    if is_boss(chat_id):
        if any(k in text.lower() for k in ['hdpe', 'ldpe', 'pp', 'pvc', 'lldpe']):
            for line in text.strip().split('\n'):
                for sep in ['-', ':']:
                    if sep in line:
                        parts = line.split(sep, 1)
                        if len(parts) == 2:
                            product = parts[0].strip()
                            price_str = ''.join(filter(str.isdigit, parts[1]))
                            if price_str:
                                current_prices[product] = int(price_str)
            if current_prices:
                await update.message.reply_text("Narxlar yangilandi!\n" + "\n".join([f"✅ {k}: {v:,}" for k, v in current_prices.items()]))
            return
        await update.message.reply_text(f"✅ Qabul: {text}")
        return
    response = await get_nargiza_response(chat_id, text)
    await update.message.reply_text(response)
    if "issiq lid" in response.lower():
        c = clients_db.get(chat_id, {})
        await notify_boss(context, f"🔥 ISSIQ LID!\nIsm: {c.get('name','?')}\nTelegram: {c.get('telegram','')}\nXabar: {text}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("🎤 Bir daqiqa...")
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            transcript = openai_client.audio.transcriptions.create(model="whisper-1", file=open(tmp.name, "rb"))
        text = transcript.text
        if text:
            response = await get_nargiza_response(chat_id, text)
            await update.message.reply_text(f"🎤 {text}\n\n{response}")
        else:
            await update.message.reply_text("Tushunmadim, matn yozing.")
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Xato. Matn yozing.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_boss(chat_id):
        context.bot_data['last_photo'] = update.message.photo[-1].file_id
        await update.message.reply_text("✅ Rasm saqlandi!")
        return
    response = await get_nargiza_response(chat_id, "Mijoz rasm yubordi")
    await update.message.reply_text(response)
    if BOSS_CHAT_ID:
        try:
            await context.bot.forward_message(chat_id=BOSS_CHAT_ID, from_chat_id=chat_id, message_id=update.message.message_id)
        except:
            pass


async def cmd_narx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id): return
    await update.message.reply_text("Narxlarni yuboring:\nHDPE 1561 - 16700\nLDPE 158 - 15200")


async def cmd_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id): return
    report = f"📊 {datetime.now().strftime('%Y-%m-%d %H:%M')}\nMijozlar: {len(clients_db)}\n"
    if current_prices:
        report += "\nNarxlar:\n" + "\n".join([f"• {p}: {v:,}" for p, v in current_prices.items()])
    await update.message.reply_text(report)


async def cmd_yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id): return
    await update.message.reply_text("/narx - narx\n/hisobot - hisobot\n/yordam - yordam")


async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("narx", cmd_narx))
    app.add_handler(CommandHandler("hisobot", cmd_hisobot))
    app.add_handler(CommandHandler("yordam", cmd_yordam))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Nargiza ishga tushdi!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await asyncio.sleep(float('inf'))


if __name__ == "__main__":
    asyncio.run(main())
