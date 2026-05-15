import os
import re
import logging
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
# {customer_chat_id: {'marka': str, 'taklif_narx': str, 'asl_narx': str}}
pending_price_negotiations = {}
# {customer_chat_id: {'marka': str, 'miqdor': str}}
pending_price_requests = {}


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
9. HECH QACHON HDPE, LDPE, LLDPE, PP, PVC, ABS, PS, HIPS, GPPS, PET kabi kimyoviy nomlarni ishlatma - faqat marka nomi bilan gapir

BIZDA BOR MARKALAR:
LDPE: 153 Kazan, 158 Kazan, 108 Kazan, 153 Sibur Tomsk, 158 Sibur Tomsk, 30200 Sibur Tomsk, 0200 Iran, 158 Socar, 158 Belarus, 158 Ufa, 2102 Laleh, 2102 Campaund, 2119 Arya Sasol, 2420D Amir Kabir, 120 LG Korea
LLDPE: 0120 Shurtan, 0220 Shurtan, 0320 Shurtan, FY920 Shurtan, 20200 Sibur Tomski, 22B02 Iran, 22b03 Iran, 0209 Shazand, 0209 Amir Kabir, LL235 Jam Iran, 0525 Shurtan, 1625 Shurtan
HDPE: 0760 Shurtan, OY-762 Shurtan, 0754 Shurtan, 1561 Shurtan, Pe 4850 Jam Iran, 2560 Shurtan, J2200 UzKorGas, J2200SA, J2210SB, J2210 UzKorGas, J2210SA, G2200 UzKorGas, 52518 Jam Iran, 52518 Amir Kabir, 293 Kazan, X3 Iran, X5 Iran, X5 Marun, HF-5110 Arya Sasol, 346 Shurtan, FL7000 UzKorGas, FL7000SA, FL7000SB, F7000 MEHR Iran, F7000 ILAM Iran, G5000 UzKorGas, G7000 UzKorGas, A4009 Turkmen, MF5000 UzKorGas, MF5000SA, MF5000SB, 957 Shurtan, B-Y460 Shurtan, B-Y456 Shurtan, BL6200 UzKorGas, BL8301 UzKorGas, BL6200 BAHTAR, BL6200SB, BL6200SA, BL5200 UzKorGas, Bl3 Bahtar, BL3 Jam Iran, BL3 Marun, P-Y342 Shurtan, P-Y456 Shurtan, Pe100 Jam Iran, Pe100 Marun, Gazprom Pe100, Jam 5000s, 03490 Sibur, WC-Y434 Shurtan, WC-Y734 Shurtan, R-0333 Shurtan
PP: J150, J150SA, J160, 1870, J160SA, J320, J330, J350, J350SA, J350SB, J360, 9928, J360SA, J370, J370SA, JM350, JM370, JM370SA, JM375, JM380, 7760, J550, J550SA, J560, J570, 4826, MT55, B310, B320, B520, FR160, FR160SA, FR170, 2024 China, 2025 China, 253, FR170SA, PP5088, PPZ30, PP382, PP552, Y130, Y120, D30 Turkmen, PP5072, 030 Sibur, PP30 China, 1003 China, 1003 Qizil Qop, 1032 Lukoil, 1035 Lukoil, 1120 Lukoil, 125 Lukoil, 1380 Lukoil, S1003 Lukoil, 68 Lukoil, 273 Lukoil, PND273 Lukoil, 1102 Regal Iran, FO130, FC550, FC550SB, FC150, 200 PPR Hyosung, 4401 PPR, 003 PPR, 100 PPR Hyosung, Sibur 003 Sibex
ABS: 750SW Kumho Korea, 121HI LG Korea, GP35 Korea, 0150 Iran, 50N Iran, Chimei 757 Taiwan, SAN 80HF Korea
PS: HIPS 825 Nijnekamsk, HIPS 7240, HIPS 4512 Iran, 525EM Nijnekamsk, G32N GPPS Iran, 500 GPPS, 1551 GPPS, 1540 GPPS Iran, 0402 GPPS Iran, 1161 GPPS Iran
PVC: PVC Tianye SG5, PVC Tianye SG3, PVC Jontai SG5, PVC Jontai SG8, PVC China MG8, PVC Navoiy SG5, PVC Yuxva
PET: Pet Jade 8816, Pet Jade 328, Pet Jade 302, PET Wankai 801, PET Wankai 881, PET Wankai 821, EPlAST Pet

NARXLAR (bugungi):
{prices}

MARKA SO'RALGANDA:
a. NARXLAR bo'limida narxi ko'rsatilgan bo'lsa: "Ha, [marka] bor. Narxi [narx] so'm/kg. Qancha kerak?"
b. NARXLAR bo'limida yo'q, lekin BIZDA BOR MARKALAR ro'yxatida bor bo'lsa: "Ha, bor. Qancha kerak?"
c. Hech birida yo'q bo'lsa: "Aniqlab beraman" de, keyin yangi qatorda:
NOMA_LUM_MARKA: [so'ralgan marka nomi]

MIJOZ UMUMIY POLIMER NOMI BILAN SO'RASA (PP bor mi, HDPE bor mi va h.k.):
- "Ha, qaysi markasi kerak?" de

ANIQ TEXNIK MA'LUMOT (MFI, zichlik, xarakteristika) kerak bo'lsa:
"Texnik ma'lumotni aniqlab beraman" de, keyin:
TEXNIK SAVOL: [mijoz ismi], [marka], [qanday ma'lumot kerak]

AFZALLIKLAR:
- 25 kg dan buyurtma
- Xarakteristika bor
- Tezkor javob 24/7

YETKAZIB BERISH:
- Yetkazib berish HECH QACHON bepul emas — bu qarorni o'zing qabul qilma
- Mijoz yetkazib berish haqida so'rasa: "Yetkazib berish narxi alohida hisoblanadi. Manzil va miqdorga qarab aniqlaymiz." de
- "Bepul yetkazish", "yetkazish narxga kiritilgan", "yetkazish bepul" kabi iboralarni HECH QACHON ishlatma

SAVDO QADAMLARI:
1. Yangi mijoz yozsa - "Xush kelibsiz!" de, ismini so'ra
2. Ism olgach - avval yangi qatorda shu formatda yoz:
ISM: [mijoz aytgan ism]
MUHIM: ISM: markerni FAQAT mijoz o'z ismini aytgandan KEYIN yoz. Ismini so'rab turganingda HECH QACHON YOZMA.
Keyin qaysi marka kerakligini so'ra
3. Marka olgach - yuqoridagi MARKA SO'RALGANDA qoidasini qo'lla
4. Miqdor olgach - to'lov turini so'ra (naqd yoki bank o'tkazma)
5. To'lov olgach - telefon raqamini so'ra
6. Mijoz raqam yuborgach - faqat quyidagi formatda yoz, boshqa hech narsa qo'shma:
ISSIQ_LID
Marka: [marka]
Miqdor: [miqdor]
Narx: [narx]
To'lov: [to'lov turi]

TELEFON RAQAM HAQIDA:
O'zbekistonda telefon raqamlar quyidagi formatlarda keladi - BARCHASI TO'G'RI:
- 9 ta raqam: 901234567, 998618500, 615012345
- 11 ta raqam: 99890123456
- 12 ta raqam: 998901234567
- +998 bilan: +998901234567
Mijoz 9 yoki undan ko'p raqam yuborganda - bu TELEFON RAQAM. ISSIQ_LID chiqar.
"Ha", "Yaxshi", "Xo'p", "Mayli" kabi so'zlar telefon emas - bunday holda qayta so'ra.

NARX SO'RALGANDA (mijoz "narxi qancha?" desa):
- NARXLAR bo'limida narxi bor bo'lsa - narxni ayt va savdo davom ettir
- NARXLAR bo'limida narxi yo'q bo'lsa - "Narxini bugun aniqlab sizga xabar beraman" de, keyin yangi qatorda:
NARX_KUTILMOQDA: [marka] | [mijoz aytgan miqdor, agar aytilmagan bo'lsa "?"]
- HECH QACHON "boshlig'im bilan gaplashaman" dema — bu faqat chegirma so'raganda

NARX KELISHUVI — FAQAT mijoz BIRINCHI marta "qimmat", "arzonroq qiling", "chegirma bering" desa:
Avval suhbat tarixini ko'r:
- Agar tarixda "Men boshlig'im bilan gaplashib javob beraman" allaqachon aytilgan bo'lsa →
  "Boshlig'imga yetkazdim allaqachon, tez orada javob beramiz" de. NARX_KELISHUV YOZMA.
- Agar tarixda narx kelishilgan (mijoz "qabul", "mayli", "xo'p" degan) bo'lsa →
  "Bu narx bo'yicha allaqachon kelishdik — [narx] so'm/kg yakuniy narximiz" de. NARX_KELISHUV YOZMA.
- Agar birinchi marta so'ralyapti → narxni o'zingdan pasaytira OLMAYSAN:
  1. Mijozga: "Men boshlig'im bilan gaplashib, sizga javob beraman." de
  2. Keyin yangi qatorda:
NARX_KELISHUV: [marka] | chegirma so'radi | [asl narx]

MIJOZ "BOSHLIG'INGIZ JAVOB BERDI" DESA:
- Avval suhbatda muhokama qilingan narxni eslaydi
- "Ha, [narx] so'm/kg qabul qilindi. To'lov turini tasdiqlaysizmi?" de

E'TIROZLAR:
"Qimmat" desa:
- "Qayerda ko'rdingiz?" de
- Narx aytsa: "Men boshlig'im bilan gaplashib, sizga javob beraman." de, keyin:
NARX_KELISHUV: [marka] | taklif: [mijoz aytgan narx] | [asl narx]

"O'ylab ko'raman" desa:
- "Narxdan tashqari boshqa savol bormi?"
- "Yo'q" desa: "Qachon qaror qilasiz?"

"Boshqa joy arzon" desa:
- "Qancha farq bor?" de
- Narx farqini aytsa: "Men boshlig'im bilan gaplashib, sizga javob beraman." de, keyin:
NARX_KELISHUV: [marka] | raqobat: [raqobat narxi] | [asl narx]

"Shunchaki narx so'radim" desa:
- NARXLAR bo'limida bor bo'lsa narxni ayt
- Yo'q bo'lsa: "Narxini aniqlab sizga xabar beraman"""


def get_prices_text():
    if not current_prices:
        return "Kiritilmagan"
    return "\n".join([f"{p}: {v:,} so'm/kg" for p, v in current_prices.items()])


def parse_price_list(text):
    prices = {}
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        for sep in [' - ', ': ', '-', ':']:
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2:
                    brand = parts[0].strip()
                    price_str = ''.join(filter(str.isdigit, parts[1]))
                    if price_str and brand:
                        prices[brand] = int(price_str)
                        break
    return prices


async def get_nargiza_response(chat_id, user_message):
    if chat_id not in conversations:
        conversations[chat_id] = []
    conversations[chat_id].append({"role": "user", "content": user_message})
    if len(conversations[chat_id]) > 20:
        conversations[chat_id] = conversations[chat_id][-20:]
    try:
        response = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=SYSTEM_PROMPT.format(prices=get_prices_text()),
            messages=conversations[chat_id]
        )
        msg = response.content[0].text.strip()
        conversations[chat_id].append({"role": "assistant", "content": msg})
        return msg
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return ""


def is_boss(chat_id):
    return chat_id == BOSS_CHAT_ID


async def notify_boss(context, message):
    if BOSS_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=BOSS_CHAT_ID, text=message)
        except Exception as e:
            logger.error(f"Boss notify error: {e}")


async def send_customer(context, chat_id, text):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error(f"Customer notify error: {e}")


def extract_phone(text):
    cleaned = re.sub(r'[\s\-\(\)]', '', text)
    match = re.search(r'\+?\d{9,13}', cleaned)
    return match.group() if match else ''


def has_valid_phone(text):
    # 9 ta raqam yetarli (998618500, 901234567, +998901234567 barchasi to'g'ri)
    digits = re.sub(r'\D', '', text)
    return len(digits) >= 9 and not all(c.isalpha() for c in text.strip())


def build_lead_card(chat_id, phone_text, response_text):
    c = clients_db.get(chat_id, {})
    details = {}
    for line in response_text.strip().split('\n')[1:]:
        if ':' in line:
            key, val = line.split(':', 1)
            details[key.strip()] = val.strip()
    tolov = details.get("To'lov", '?')
    return (
        f"ISSIQ LID!\n"
        f"Ism: {c.get('name', '?')}\n"
        f"Telegram: {c.get('telegram', 'nomalum')}\n"
        f"Telefon: {extract_phone(phone_text)}\n"
        f"Marka: {details.get('Marka', '?')}\n"
        f"Miqdor: {details.get('Miqdor', '?')}\n"
        f"Narx: {details.get('Narx', '?')}\n"
        f"To'lov: {tolov}"
    )



# Mijozga ko'rsatilmaydigan ichki markerlar
_INTERNAL = (
    'ISSIQ_LID', 'ISM:', 'NOMA_LUM_MARKA:', 'TEXNIK SAVOL:',
    'NARX_KELISHUV:', "NARX_SO'ROV:", 'NARX_SOROV:', 'STOK_TEKSHIR:', 'NARX_KUTILMOQDA:',
)


def parse_response(response):
    lines = response.strip().split('\n')
    customer_lines = []
    markers = {}
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith('ISSIQ_LID'):
            markers['issiq_lid'] = response
        elif upper.startswith('ISM:'):
            markers['ism'] = stripped.split(':', 1)[1].strip()
        elif upper.startswith('NOMA_LUM_MARKA:'):
            markers['noma_lum_marka'] = stripped.split(':', 1)[1].strip()
        elif upper.startswith('TEXNIK SAVOL:'):
            markers['texnik_savol'] = stripped.split(':', 1)[1].strip()
        elif upper.startswith('NARX_KELISHUV:'):
            markers['narx_kelishuv'] = stripped.split(':', 1)[1].strip()
        elif upper.startswith('NARX_KUTILMOQDA:'):
            markers['narx_kutilmoqda'] = stripped.split(':', 1)[1].strip()
        elif any(upper.startswith(m.upper()) for m in _INTERNAL):
            pass  # ichki marker — mijozga ko'rsatilmaydi
        else:
            customer_lines.append(line)
    return '\n'.join(customer_lines).strip(), markers


async def handle_response(chat_id, text, response, update, context):
    customer_text, markers = parse_response(response)

    if 'issiq_lid' in markers:
        if not has_valid_phone(text):
            # Raqam emas (masalan "Ha olaman") — qayta so'ra
            await update.message.reply_text("Telefon raqamingizni yuboring.")
            if chat_id in conversations and conversations[chat_id]:
                conversations[chat_id][-1]['content'] = "Telefon raqamini so'radim."
            return
        await update.message.reply_text("Rahmat, tez orada bog'lanamiz.")
        await notify_boss(context, build_lead_card(chat_id, text, response))
        if chat_id in clients_db:
            clients_db[chat_id]['category'] = 'Issiq'
        if chat_id in conversations and conversations[chat_id]:
            conversations[chat_id][-1]['content'] = "Rahmat, tez orada bog'lanamiz."
        return

    if 'ism' in markers:
        name = markers['ism']
        if chat_id in clients_db:
            clients_db[chat_id]['name'] = name
        else:
            clients_db[chat_id] = {
                'name': name,
                'telegram': '',
                'category': 'Yangi',
                'date': datetime.now().strftime("%Y-%m-%d %H:%M")
            }

    if 'noma_lum_marka' in markers:
        brand = markers['noma_lum_marka']
        msg = customer_text or "Aniqlab beraman."
        await update.message.reply_text(msg)
        c = clients_db.get(chat_id, {})
        await notify_boss(
            context,
            f"Mijoz {brand} so'rayapti. Bor yoki yo'qmi?\n"
            f"Mijoz: {c.get('name', '?')} {c.get('telegram', '')}"
        )
        return

    if customer_text:
        await update.message.reply_text(customer_text)

    if 'texnik_savol' in markers:
        c = clients_db.get(chat_id, {})
        await notify_boss(
            context,
            f"TEXNIK SAVOL:\n"
            f"Mijoz: {c.get('name', '?')} {c.get('telegram', '')}\n"
            f"Xabar: {text}"
        )

    if 'narx_kelishuv' in markers:
        c = clients_db.get(chat_id, {})
        parts = [p.strip() for p in markers['narx_kelishuv'].split('|')]
        marka = parts[0] if parts else '?'
        vaziyat = parts[1] if len(parts) > 1 else "chegirma so'radi"
        asl = parts[2] if len(parts) > 2 else str(current_prices.get(marka, '?'))
        pending_price_negotiations[chat_id] = {
            'marka': marka, 'taklif_narx': vaziyat, 'asl_narx': asl
        }
        if vaziyat.lower().startswith('raqobat:'):
            raqobat_narx = vaziyat.split(':', 1)[1].strip()
            vaziyat_text = f"Mijoz raqobatchi narxini aytdi: {raqobat_narx} so'm/kg"
        elif vaziyat.lower().startswith('taklif:'):
            taklif_narx = vaziyat.split(':', 1)[1].strip()
            vaziyat_text = f"Mijoz taklif narxi: {taklif_narx} so'm/kg"
        else:
            vaziyat_text = vaziyat
        try:
            asl_fmt = f"{int(re.sub(r'[^0-9]', '', str(asl))):,}" if re.search(r'\d', str(asl)) else asl
        except (ValueError, TypeError):
            asl_fmt = asl
        await notify_boss(
            context,
            f"NARX KELISHUVI:\n"
            f"Mijoz: {c.get('name', '?')} {c.get('telegram', '')}\n"
            f"Marka: {marka}\n"
            f"{vaziyat_text}\n"
            f"Bizning narx: {asl_fmt} so'm/kg\n"
            f"Javob: 'ha [narx]' yoki 'yo\\'q'"
        )

    if 'narx_kutilmoqda' in markers:
        parts = [p.strip() for p in markers['narx_kutilmoqda'].split('|')]
        marka = parts[0] if parts else '?'
        miqdor = parts[1] if len(parts) > 1 else '?'
        pending_price_requests[chat_id] = {'marka': marka, 'miqdor': miqdor}


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
        low = text.lower().strip()

        if context.bot_data.get('awaiting_narx'):
            context.bot_data['awaiting_narx'] = False
            parsed = parse_price_list(text)
            if parsed:
                current_prices.update(parsed)
                prices_text = "\n".join([f"{k}: {v:,}" for k, v in parsed.items()])
                await update.message.reply_text(f"Narxlar saqlandi!\n{prices_text}")
                # Narx kutayotgan mijozlarga xabar yuborish
                notified = []
                for cust_id, req in list(pending_price_requests.items()):
                    marka = req.get('marka', '')
                    if marka in parsed:
                        narx = parsed[marka]
                        miqdor_str = req.get('miqdor', '?')
                        msg = f"{marka} narxi: {narx:,} so'm/kg."
                        if miqdor_str and miqdor_str != '?':
                            try:
                                digits = int(re.sub(r'[^0-9]', '', miqdor_str))
                                if 'tonn' in miqdor_str.lower():
                                    kg = digits * 1000
                                else:
                                    kg = digits
                                total = narx * kg
                                msg += f" {miqdor_str} uchun jami: {total:,} so'm."
                            except (ValueError, TypeError):
                                pass
                        await send_customer(context, cust_id, msg)
                        pending_price_requests.pop(cust_id, None)
                        notified.append(marka)
                if notified:
                    await update.message.reply_text(
                        f"Narx kutayotgan mijozlarga xabar yuborildi: {', '.join(notified)}"
                    )
            else:
                await update.message.reply_text("Format noto'g'ri. Qaytadan /narx yuboring.")
            return

        # Boss narx kelishuvini tasdiqlaydi: "ha 20400", "ruxsat berdi", "tasdiqlandi" va h.k.
        if pending_price_negotiations and any(w in low for w in [
            'ha ', 'ruxsat', 'tasdiqlandi', 'berish mumkin', 'beramiz', 'roziman', 'ok'
        ]):
            price_match = re.search(r'\d[\d\s]*\d|\d{4,}', text)
            customer_id, data = list(pending_price_negotiations.items())[-1]
            agreed = re.sub(r'\s', '', price_match.group()) if price_match else data['taklif_narx']
            c = clients_db.get(customer_id, {})
            try:
                agreed_fmt = f"{int(agreed):,}"
            except (ValueError, TypeError):
                agreed_fmt = agreed
            await send_customer(
                context, customer_id,
                f"Yaxshi xabar! {data['marka']} narxi {agreed_fmt} so'm/kg qabul qilindi. "
                f"To'lov turini tasdiqlaysizmi?"
            )
            if customer_id in clients_db:
                clients_db[customer_id]['agreed_narx'] = agreed
            pending_price_negotiations.pop(customer_id, None)
            await update.message.reply_text(f"Mijozga {agreed_fmt} so'm/kg narx yuborildi.")
            return

        if pending_price_negotiations and any(w in low for w in ["yo'q", 'yoq', 'mumkin emas', 'rad']):
            customer_id, data = list(pending_price_negotiations.items())[-1]
            await send_customer(
                context, customer_id,
                f"{data['marka']} narxi {data['asl_narx']} so'm/kg — bu bizning yakuniy narximiz."
            )
            pending_price_negotiations.pop(customer_id, None)
            await update.message.reply_text("Mijozga yakuniy narx yuborildi.")
            return

        return

    response = await get_nargiza_response(chat_id, text)
    await handle_response(chat_id, text, response, update, context)


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
            await handle_response(chat_id, text, response, update, context)
        else:
            await update.message.reply_text("Tushunmadim, matn yozing.")
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Ovozni tushunmadim, matn yozing.")


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
    context.bot_data['awaiting_narx'] = True
    await update.message.reply_text(
        "Narxlarni yuboring (har qator alohida):\n\n"
        "1561 - 12500\n"
        "0220 - 11000\n"
        "FR170 - 13000"
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
        "Mijoz stok so'raganda:\n"
        "Ha/Bor - tasdiq, ISSIQ LID yuboriladi\n"
        "Yo'q/Sotildi - mijozga tugaganligi aytiladi"
    )


def main():
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
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
