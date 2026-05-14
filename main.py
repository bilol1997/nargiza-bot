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

SYSTEM_PROMPT = """Sen Nargiza - Petro Plast kompaniyasining savdo menedjeri.

ASOSIY QOIDALAR:
1. Qisqa yoz - 1-2 jumla, hech qachon ko'p emas
2. Hech qachon ** yoki markdown ishlatma
3. Tabiiy, oddiy gapir - do'st kabi
4. Bot ekanligingni hech qachon aytma
5. O'zbek yoki rus tilida gapir - mijoz qaysi tilda yozsa
6. Emoji ishlatma
7. Imlo xatosiz yoz
8. Savol bersang - faqat bitta savol ber

MAHSULOT BILIMI (nima ishlab chiqarilishini bilasan):
HDPE - quvur, idish, kanistr, flakon, plyonka, qop, monofilament, ip
LDPE - yupqa plyonka, paket, laminatsiya, qishloq xo'jaligi plyonkasi
LLDPE - stretch plyonka, mustahkam paket, qishloq xo'jaligi plyonkasi
PP homo - qop ip, raffia, BOPP plyonka, to'qilgan qop
PP block - zarbga chidamli idish, sanoat detallari, qopqoq
PP random - shaffof idish, tibbiy mahsulot, oziq-ovqat qadoqlash
PPR - issiq va sovuq suv quvurlari, fittinglar
ABS - elektronika korpusi, avtomobil detallari, maishiy texnika
HIPS - muzlatkich qoplamasi, reklama listi, quyma mahsulot
GPPS - shaffof quyma mahsulot, disposable idish
PVC - profil, quvur, kabel qoplamasi, oyna romasi
PET - suv shishasi, ichimlik idishi, qadoqlash

ANIQ TEXNIK MA'LUMOT (MFI, zichlik, xarakteristika) kerak bo'lsa:
"Bir daqiqa, texnik ma'lumotni aniqlab beraman" de va Bossga yubor:
TEXNIK SAVOL: [mijoz ismi], [marka], [qanday ma'lumot kerak]

NARXLAR:
{prices}

AFZALLIKLAR:
- Haftada 1 kun Toshkent ichida bepul yetkazish
- 25 kg dan buyurtma
- Xarakteristika bor
- Tezkor javob 24/7

SAVDO QADAMLARI:
1. Yangi mijoz yozsa - salom, ismini so'ra
2. Ism olgach - qaysi mahsulot kerakligini so'ra
3. Mahsulot olgach - qancha kerakligini so'ra
4. Miqdor olgach - narx ayt va to'lov turini so'ra
5. Tayyor bo'lsa - telefon raqamini so'ra
6. Raqam olgach - FAQAT "ISSIQ_LID" so'zini yoz (boshqa hech narsa yozma)

E'TIROZLAR:
"Qimmat" desa:
- "Qayerda ko'rdingiz?"
- Narx aytsa: "Agar men ham o'sha narxda qilsam, olasizmi?"
- "Ha" desa: "Qancha kerak va qachon?"

"O'ylab ko'raman" desa:
- "Narxdan tashqari boshqa savol bormi?"
- "Yo'q" desa: "Qachon qaror qilasiz?"

"Boshqa joy arzon" desa:
- "Qancha farq bor?"
- "U yerdan avval olganmisiz?"
- "Yo'q" desa: "Sinab ko'ring bizni, keyin taqqoslaysiz"

"Shunchaki narx so'radim" desa:
- "Tushundim. Qaysi mahsulot ishlab chiqarasiz?"
- Javob bergach: "Oyiga taxminan qancha kerak?"

DOIMIY MIJOZ QILISH:
Birinchi sotuvdan 3 kun o'tib: "Salom [ism], xomashyo qanday keldi? Keyingi partiya qachon kerak?"""


def get_prices_text():
    if not current_prices:
        return "Bugungi narxlar kiritilmagan"
    return "\n".join([f"{p}: {v:,} so'm/kg" for p, v in current_prices.items()])


async def get_nargiza_response(chat_id, user_message):
    if chat_id not in conversations:
        conversations[chat_id] = []
    conversations[chat_id].append({"role": "user", "content": user_message})
    if len(conversations[chat_id]) > 20:
        conversations[chat_id] = conversations[chat_id][-20:]
    try:
        response = claude_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=SYSTEM_PROMPT.format(prices=get_prices_text()),
            messages=conversations[chat_id]
        )
        msg = response.content[0].text.strip()
        conversations[chat_id].append({"role": "assistant", "content": msg})
        return msg
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return "Kechirasiz, texnik nosozlik. Qaytadan yozing."


def is_boss(chat_id):
    return chat_id == BOSS_CHAT_ID


async def notify_boss(context, message):
    if BOSS_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=BOSS_CHAT_ID, text=message)
        except Exception as e:
            logger.error(f"Boss notify error: {e}")


def build_lead_card(chat_id, user_message):
    c = clients_db.get(chat_id, {})
    conv = conversations.get(chat_id, [])
    conv_text = "\n".join([f"{m['role']}: {m['content']}" for m in conv[-10:]])
    return (
        f"ISSIQ LID!\n"
        f"Ism: {c.get('name', '?')}\n"
        f"Telegram: {c.get('telegram', 'noma`lum')}\n"
        f"Chat ID: {chat_id}\n"
        f"Telefon: {user_message}\n\n"
        f"Suhbat:\n{conv_text}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_boss(chat_id):
        await update.message.reply_text(
            "Salom Boss!\n"
            "/narx - narx kiritish\n"
            "/hisobot - hisobot\n"
            "/mijozlar - mijozlar royxati\n"
            "/yordam - yordam"
        )
    else:
        response = await get_nargiza_response(chat_id, "Salom, birinchi marta yozayapman")
        await update.message.reply_text(response)
        clients_db[chat_id] = {
            "name": update.effective_user.first_name or "",
            "telegram": f"@{update.effective_user.username}" if update.effective_user.username else "",
            "category": "Yangi",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        }


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    if is_boss(chat_id):
        if any(k in text.lower() for k in ['hdpe', 'ldpe', 'pp', 'pvc', 'lldpe', 'abs', 'hips', 'gpps', 'pet', 'ppr']):
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
                prices_text = "\n".join([f"{k}: {v:,}" for k, v in current_prices.items()])
                await update.message.reply_text(f"Narxlar yangilandi!\n{prices_text}")
            return
        await update.message.reply_text(f"Qabul: {text}")
        return

    response = await get_nargiza_response(chat_id, text)

    if "issiq_lid" in response.lower():
        # Mijozga faqat oddiy javob, kartochka ko'rsatilmaydi
        await update.message.reply_text("Rahmat, tez orada bog'lanamiz.")
        # Bossga to'liq ichki kartochka yuboriladi
        await notify_boss(context, build_lead_card(chat_id, text))
        if chat_id in clients_db:
            clients_db[chat_id]['category'] = 'Issiq'
    else:
        await update.message.reply_text(response)

    if "texnik savol" in response.lower():
        c = clients_db.get(chat_id, {})
        await notify_boss(
            context,
            f"TEXNIK SAVOL:\n"
            f"Mijoz: {c.get('name', '?')} {c.get('telegram', '')}\n"
            f"Xabar: {text}"
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("Bir daqiqa...")
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=open(tmp.name, "rb")
            )
        text = transcript.text
        if text:
            response = await get_nargiza_response(chat_id, text)
            if "issiq_lid" in response.lower():
                await update.message.reply_text("Rahmat, tez orada bog'lanamiz.")
                await notify_boss(context, build_lead_card(chat_id, text))
                if chat_id in clients_db:
                    clients_db[chat_id]['category'] = 'Issiq'
            else:
                await update.message.reply_text(f"{text}\n\n{response}")
        else:
            await update.message.reply_text("Tushunmadim, matn yozing.")
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Ovoz xabarni qabul qila olmadim, matn yozing.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_boss(chat_id):
        context.bot_data['last_photo'] = update.message.photo[-1].file_id
        await update.message.reply_text("Rasm saqlandi!")
        return
    response = await get_nargiza_response(chat_id, "Mijoz rasm yubordi")
    await update.message.reply_text(response)
    if BOSS_CHAT_ID:
        try:
            await context.bot.forward_message(
                chat_id=BOSS_CHAT_ID,
                from_chat_id=chat_id,
                message_id=update.message.message_id
            )
        except Exception as e:
            logger.error(f"Photo forward error: {e}")


async def cmd_narx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    await update.message.reply_text(
        "Narxlarni yuboring:\n"
        "HDPE 1561 - 16700\n"
        "LDPE 158 - 15200\n"
        "PP H030 - 17500"
    )


async def cmd_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return

    total = len(clients_db)
    yangi = sum(1 for c in clients_db.values() if c.get('category') == 'Yangi')
    issiq = sum(1 for c in clients_db.values() if c.get('category') == 'Issiq')
    sovuq = sum(1 for c in clients_db.values() if c.get('category') == 'Sovuq')

    report = (
        f"Hisobot - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Jami mijozlar: {total}\n"
        f"Yangi: {yangi}\n"
        f"Issiq lid: {issiq}\n"
        f"Sovuq lid: {sovuq}\n"
    )

    if current_prices:
        report += "\nHozirgi narxlar:\n"
        report += "\n".join([f"{p}: {v:,} som" for p, v in current_prices.items()])

    await update.message.reply_text(report)


async def cmd_mijozlar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return

    if not clients_db:
        await update.message.reply_text("Hali mijoz yoq.")
        return

    text = "Mijozlar:\n\n"
    for chat_id, c in list(clients_db.items())[-10:]:
        text += f"{c.get('name', '?')} {c.get('telegram', '')} - {c.get('category', '?')}\n"

    await update.message.reply_text(text)


async def cmd_yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    await update.message.reply_text(
        "Buyruqlar:\n"
        "/narx - narx kiritish\n"
        "/hisobot - statistika\n"
        "/mijozlar - songi mijozlar\n"
        "/yordam - shu menyu\n\n"
        "Narx kiritish:\n"
        "HDPE 1561 - 16700\n"
        "LDPE 158 - 15200"
    )


async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("narx", cmd_narx))
    app.add_handler(CommandHandler("hisobot", cmd_hisobot))
    app.add_handler(CommandHandler("mijozlar", cmd_mijozlar))
    app.add_handler(CommandHandler("yordam", cmd_yordam))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Nargiza ishga tushdi!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    await asyncio.sleep(float('inf'))


if __name__ == "__main__":
    asyncio.run(main())
