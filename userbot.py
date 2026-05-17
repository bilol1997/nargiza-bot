import asyncio
import logging
import os
import re
from datetime import datetime

import anthropic
import pytz
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

API_ID         = int(os.environ["API_ID"])
API_HASH       = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
BOSS_CHAT_ID   = int(os.environ["BOSS_CHAT_ID"])
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]

TASHKENT = pytz.timezone("Asia/Tashkent")

claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

last_price_message: str | None = None
conversations: dict = {}   # {chat_id: [{"role": ..., "content": ...}]}
clients_db: dict = {}      # {chat_id: {"name": ..., "telegram": ...}}


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
c. Hech birida yo'q bo'lsa: "Aniqlab beraman" de

MIJOZ UMUMIY POLIMER NOMI BILAN SO'RASA (PP bor mi, HDPE bor mi va h.k.):
- "Ha, qaysi markasi kerak?" de

ANIQ TEXNIK MA'LUMOT (MFI, zichlik, xarakteristika) kerak bo'lsa:
"Texnik ma'lumotni aniqlab beraman" de

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
- NARXLAR bo'limida narxi yo'q bo'lsa - "Narxini bugun aniqlab sizga xabar beraman" de
- HECH QACHON "boshlig'im bilan gaplashaman" dema — bu faqat chegirma so'raganda

NARX KELISHUVI — FAQAT mijoz BIRINCHI marta "qimmat", "arzonroq qiling", "chegirma bering" desa:
Avval suhbat tarixini ko'r:
- Agar tarixda "Men boshlig'im bilan gaplashib javob beraman" allaqachon aytilgan bo'lsa →
  "Boshlig'imga yetkazdim allaqachon, tez orada javob beramiz" de.
- Agar birinchi marta so'ralyapti → narxni o'zingdan pasaytira OLMAYSAN:
  "Men boshlig'im bilan gaplashib, sizga javob beraman." de

E'TIROZLAR:
"Qimmat" desa: "Qayerda ko'rdingiz?" de
"O'ylab ko'raman" desa: "Narxdan tashqari boshqa savol bormi?"
"Boshqa joy arzon" desa: "Qancha farq bor?" de

MUDDATLI TO'LOV SO'RASA:
- "Hozircha to'lov naqd yoki bank o'tkazma orqali amalga oshiriladi." de

KATTA MIQDOR (5 tonna va undan ko'p) SO'RASA:
- Narxni aytgandan so'ng: "Katta miqdor uchun qo'shimcha chegirma bo'lishi mumkin — aniqlayman." de

SIFAT HUJJATI YOKI SERTIFIKAT SO'RASA:
- "Ha, barcha mahsulotlarda sertifikat bor. Kerakli markani ayting, yuboray." de

MAHSULOT QACHON KELISHI SO'RASA:
- "Mavjud stokdan — 1-2 ish kuni ichida. Buyurtma bo'lsa — alohida aniqlayman." de"""


# ── Yordamchi funksiyalar ──────────────────────────────────────────────────────

def parse_price_list(text: str) -> dict:
    prices = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for sep in [" - ", ": ", "-", ":"]:
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2:
                    brand = parts[0].strip()
                    price_str = "".join(filter(str.isdigit, parts[1]))
                    if price_str and brand:
                        prices[brand] = int(price_str)
                        break
    return prices


def get_prices_text() -> str:
    if not last_price_message:
        return "Kiritilmagan"
    prices = parse_price_list(last_price_message)
    if not prices:
        return last_price_message
    return "\n".join(f"{k}: {v:,} so'm/kg" for k, v in prices.items())


def extract_phone(text: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", text)
    m = re.search(r"\+?\d{9,13}", cleaned)
    return m.group() if m else ""


def has_valid_phone(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 9 and not all(c.isalpha() for c in text.strip())


TOLOV_KEY = "To’lov"

_INTERNAL_MARKERS = (
    "ISSIQ_LID", "ISM:", "NARX_KELISHUV:", "NARX_KUTILMOQDA:",
    "NOMA_LUM_MARKA:", "TEXNIK SAVOL:",
)


def parse_response(response: str) -> tuple[str, dict]:
    lines = response.strip().split("\n")
    customer_lines = []
    markers = {}
    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("ISSIQ_LID"):
            markers["issiq_lid"] = response
        elif upper.startswith("ISM:"):
            markers["ism"] = line.strip().split(":", 1)[1].strip()
        elif any(upper.startswith(m.upper()) for m in _INTERNAL_MARKERS):
            pass
        else:
            customer_lines.append(line)
    return "\n".join(customer_lines).strip(), markers


def build_lead_card(chat_id: int, phone_text: str, response_text: str) -> str:
    c = clients_db.get(chat_id, {})
    details = {}
    for line in response_text.strip().split("\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            details[k.strip()] = v.strip()
    return (
        f"ISSIQ LID! (userbot)\n"
        f"Ism: {c.get('name', '?')}\n"
        f"Telegram: {c.get('telegram', 'nomalum')}\n"
        f"Telefon: {extract_phone(phone_text)}\n"
        f"Marka: {details.get('Marka', '?')}\n"
        f"Miqdor: {details.get('Miqdor', '?')}\n"
        f"Narx: {details.get('Narx', '?')}\n"
        f"To'lov: {details.get(TOLOV_KEY, '?')}"
    )


async def get_ai_response(chat_id: int, user_message: str) -> str:
    if chat_id not in conversations:
        conversations[chat_id] = []
    conversations[chat_id].append({"role": "user", "content": user_message})
    if len(conversations[chat_id]) > 20:
        conversations[chat_id] = conversations[chat_id][-20:]
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=SYSTEM_PROMPT.format(prices=get_prices_text()),
            messages=conversations[chat_id],
        )
        msg = resp.content[0].text.strip()
        conversations[chat_id].append({"role": "assistant", "content": msg})
        return msg
    except Exception as e:
        logger.error(f"Claude xatosi: {e}")
        return "Bir daqiqa kuting."


# ── Event handlerlar ───────────────────────────────────────────────────────────

@client.on(events.NewMessage(incoming=True))
async def on_incoming_message(event):
    sender_id = event.sender_id
    text = (event.message.text or "").strip()
    logger.info(f"Xabar keldi: {sender_id} - {text[:80]!r}")

    if not event.is_private:
        return

    # BOSS narx xabari yuborsa — saqlash
    if sender_id == BOSS_CHAT_ID:
        global last_price_message
        if text:
            last_price_message = text
            logger.info("BOSS yangi narx xabari saqlandi.")
        return

    # Mijoz xabari
    if not text:
        return

    if sender_id not in clients_db:
        sender = await event.get_sender()
        username = f"@{sender.username}" if getattr(sender, "username", None) else ""
        clients_db[sender_id] = {
            "name": getattr(sender, "first_name", "") or "",
            "telegram": username,
        }
        logger.info(f"Yangi mijoz: {clients_db[sender_id]['name']} {username}")

    response = await get_ai_response(sender_id, text)
    customer_text, markers = parse_response(response)

    if "ism" in markers:
        clients_db[sender_id]["name"] = markers["ism"]

    if "issiq_lid" in markers:
        if not has_valid_phone(text):
            await event.respond("Telefon raqamingizni yuboring.")
            if conversations.get(sender_id):
                conversations[sender_id][-1]["content"] = "Telefon raqamini so'radim."
            return
        await event.respond("Rahmat, tez orada bog'lanamiz.")
        card = build_lead_card(sender_id, text, response)
        await client.send_message(BOSS_CHAT_ID, card)
        logger.info(f"Issiq lid BOSS ga yuborildi: {clients_db[sender_id].get('name', sender_id)}")
        if conversations.get(sender_id):
            conversations[sender_id][-1]["content"] = "Rahmat, tez orada bog'lanamiz."
        return

    if customer_text:
        await event.respond(customer_text)


# ── Guruh e'lonlari ────────────────────────────────────────────────────────────

async def get_all_groups() -> list:
    groups = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, (Chat, Channel)):
            if isinstance(entity, Channel) and entity.broadcast:
                continue
            groups.append(dialog)
    return groups


async def send_to_all_groups():
    if not last_price_message:
        logger.info("BOSS hali narx xabari yubormagan.")
        return
    groups = await get_all_groups()
    if not groups:
        logger.warning("Hech qanday guruh topilmadi.")
        return
    ok, fail = 0, 0
    for dialog in groups:
        try:
            await client.send_message(dialog.id, last_price_message)
            logger.info(f"  [OK] {dialog.name}")
            ok += 1
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"  [XATO] {dialog.name}: {e}")
            fail += 1
    logger.info(f"E'lon natijasi: {ok} yuborildi, {fail} xato.")


async def announcer():
    while True:
        now = datetime.now(TASHKENT)
        wait_seconds = (60 - now.minute) * 60 - now.second
        logger.info(
            f"Keyingi e'lon tekshiruvi {wait_seconds // 60} daqiqadan keyin "
            f"(hozir {now.strftime('%H:%M')} Toshkent)"
        )
        await asyncio.sleep(wait_seconds)

        now = datetime.now(TASHKENT)
        if 9 <= now.hour < 22:
            logger.info(f"[{now.strftime('%H:%M')}] Barcha guruhlarga e'lon yuborilmoqda...")
            await send_to_all_groups()
        else:
            logger.info(f"[{now.strftime('%H:%M')}] Ish vaqti emas (09:00–22:00). E'lon yuborilmadi.")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    session_len = len(SESSION_STRING.strip())
    logger.info(f"SESSION_STRING uzunligi: {session_len} belgi")
    if session_len < 100:
        logger.error("SESSION_STRING juda qisqa — Railway da to'g'ri o'rnatilganmi?")
        return

    await client.start()

    me = await client.get_me()
    if me is None:
        logger.error("get_me() None qaytardi — session yaroqsiz.")
        return
    logger.info(f"Userbot ishga tushdi: id={me.id} | first_name={me.first_name!r} | username={me.username!r} | phone={me.phone!r}")

    groups = await get_all_groups()
    logger.info(f"Topilgan guruhlar ({len(groups)} ta):")
    for g in groups:
        logger.info(f"  - {g.name} (id={g.id})")

    await asyncio.gather(
        announcer(),
        client.run_until_disconnected(),
    )


asyncio.run(main())
