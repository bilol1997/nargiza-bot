import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta

from typing import Optional
from dotenv import load_dotenv
load_dotenv()

import anthropic
import pytz
import requests as _http
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat, MessageMediaContact

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as _rsa_padding
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

try:
    import db as _db
    _DB_OK = True
except Exception as _db_err:
    logger = logging.getLogger(__name__)
    logger.warning(f"Supabase db moduli yuklanmadi: {_db_err}")
    _DB_OK = False

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

CLIENTS_DB_FILE  = "clients_db.json"  # JSON fallback
_SHEETS_ID       = os.environ.get("GOOGLE_SHEET_ID", "")
_GC_RAW          = os.environ.get("GOOGLE_CREDENTIALS", "")
_MIJOZLAR_SHEET  = "Mijozlar"
_LIDLAR_SHEET    = "Lidlar"
_LIDLAR_HEADER   = ["Sana", "Ism", "Telefon", "Marka", "Miqdor", "To'lov", "Narx", "Holat",
                    "Telegram", "Status", "Til", "Sikl", "Izoh"]
_SHEETS_BASE     = f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEETS_ID}"
_token_cache: dict = {"token": None, "exp": 0}

current_prices: dict = {}  # {brand: price} — kümülatif narx bazasi
conversations: dict = {}        # {chat_id: [{"role": ..., "content": ...}]}
clients_db: dict = {}           # {chat_id: {"name": ..., "telegram": ...}}
pending_price_requests: dict = {}        # {customer_id: {"marka": str, "miqdor": str}}
pending_bank_requests: dict = {}         # {customer_id: {"marka": str, "miqdor": str}}
pending_price_negotiations: dict = {}    # {customer_id: {"marka": str, "taklif": str, "asl": str}}
pending_intent: dict = {}  # {boss_sender_id: {"queue": [...], "ts":...}}
kanonik_markalar_ro: list = []   # [{"nom":..., "bolim":...}, ...]
soz_sinonimlar_ro: dict = {}     # {"kampaund": "compound", ...}
alias_cache: dict = {}           # {"normallashtirilgan alias": "Kanonik Nom", ...}

FOLLOW_UPS_FILE  = "follow_ups.json"
PRICES_FILE      = "current_prices.json"
follow_ups: list = []
_lidlar_init_done: bool = False
_hisobot_store: dict = {}  # kunlik hisobot bir marta yuborilishi uchun flaglar


SYSTEM_PROMPT = """Sening isming — Nargiza (N-A-R-G-I-Z-A, boshqa hech qanday variant yo'q). Sen Petro Plast kompaniyasining savdo menedjeri.

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
10. Ismga murojaat: erkak O'zbek ismi bo'lsa "aka", ayol ismi bo'lsa "opa" qo'sh. Masalan: "Jasur aka", "Malika opa"

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

RANG / MAHALLIY NOM BILAN SO'RALGANDA:
- "ko'k qop" = 0209 Shazand (Iran) — LLDPE, issiqxona/qopchiq plyonkasi
- "qizil qop" = DEFAULT: 0209 Amir Kabir (Iran) — LLDPE, issiqxona/qopchiq plyonkasi.
  ISTISNO: agar suhbatda PP, bir martalik idish-tovoq yoki tola haqida gaplashilgan bo'lsa — 1003 China (PP) ni nazarda tutishi mumkin.
  Noaniq bo'lsa: aniqlashtirish savoli ber.
- Boshqa rang so'ralganda: "Qaysi mahsulot uchun kerak?" deb aniqlashtir
- Rang nomini marka raqamiga bog'lagandan so'ng — odatdagi MARKA SO'RALGANDA qoidasini qo'lla

SOHA / MAHSULOT TURI BO'YICHA SO'RALGANDA (mijoz "quvur uchun", "qopchiq uchun", "bochka uchun" kabi so'rasa):
- Issiqxona plyonkasi / parnik: LDPE (153/158 Kazan, Sibur, Socar, Belarus, Ufa, Laleh, Arya Sasol, Amir Kabir, LG) yoki LLDPE (Shurtan, Sibur, Iran, 0209 Shazand/Amir Kabir, Jam, 0525)
- Paket ishlab chiqarish: HDPE (0760/OY-762/0754/1561 Shurtan, Pe4850/52518 Jam Iran, J2200/J2210/G2200 UzKorGas, 293 Kazan, X3/X5 Iran, FL7000/G5000/G7000 UzKorGas, F7000 MEHR/ILAM)
- Katta bochka / quyma idish: BL seriya (UzKorGas, BAHTAR, Jam Iran, Marun), B-Y460/B-Y456 Shurtan
- Plastik quvur: P-Y342/P-Y456 Shurtan, Pe100 Jam/Marun, Gazprom Pe100, Jam 5000s, 03490 Sibur
- Kabel: WC-Y434/WC-Y734/R-0333 Shurtan
- Bir martalik idish-tovoq / shprits: PP J seriya (J150-J570), JM seriya, MT55
- O'yinchoq / plastik idish: 1625 Shurtan
- Pryazha / mono tola: PP FR160/FR170, Y130/Y120, D30, 030 Sibur, 1003 China va boshqalar
- So'ralgan sohaga mos markani taklif qil: "Bu uchun [marka] bor. Qancha kerak?" de

MIJOZ UMUMIY POLIMER NOMI BILAN SO'RASA (PP bor mi, HDPE bor mi va h.k.):
- "Ha, qaysi markasi kerak?" de

ANIQ TEXNIK MA'LUMOT (MFI, zichlik, xarakteristika) kerak bo'lsa:
"Texnik ma'lumotni aniqlab beraman" de

AFZALLIKLAR:
- Minimal buyurtma 500 kg
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
Keyin: "Qanday mahsulot ishlab chiqarasiz?" deb so'ra (issiqxona plyonkasi, qop, quvur va h.k.)
Soha aytilganda yangi qatorda yoz:
SOHA: [qiymat] — faqat quyidagilardan biri: issiqxona_plyonkasi / paket / qop / oyinchoq_idish / quvur / kabel / bir_martalik_idish / boshqa
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
Telefon: [mijoz yuborgan raqam]

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
- NARXLAR bo'limida narxi yo'q bo'lsa - "Aka, bir zum, narxni aniqlab beraman" de, keyin yangi qatorda:
NARX_KUTILMOQDA: [marka] | [mijoz aytgan miqdor, agar aytilmagan bo'lsa "?"]
- Mijoz aniq marka ko'rsatmay "arzonrog'ini", "shunaqa", "ana shuni" kabi noaniq so'z ishlatsa:
  suhbat tarixidan barcha muhokama qilingan markalarni ol, vergul bilan yoz:
NARX_KUTILMOQDA: [marka1, marka2] | [miqdor]
- HECH QACHON "boshlig'im bilan gaplashaman" dema — bu faqat chegirma so'raganda

NARX KELISHUVI — FAQAT mijoz BIRINCHI marta "qimmat", "arzonroq qiling", "chegirma bering" desa:
Avval suhbat tarixini ko'r:
- Agar tarixda "Men boshlig'im bilan gaplashib javob beraman" allaqachon aytilgan bo'lsa →
  "Boshlig'imga yetkazdim allaqachon, tez orada javob beramiz" de.
- Agar birinchi marta so'ralyapti → narxni o'zingdan pasaytira OLMAYSAN:
  1. Mijozga: "Men boshlig'im bilan gaplashib, sizga javob beraman." de
  2. Keyin yangi qatorda:
NARX_KELISHUV: [marka] | [mijoz taklif qilgan narx, yo'q bo'lsa "?"] | [joriy narx, yo'q bo'lsa "?"]

E'TIROZLAR — QOIDALAR:
- HECH QACHON narxni o'zing tushirma, chegirma va'da qilma, shartlarni o'zgartirma
- Chegirma so'rasa: "Buni rahbarim bilan tekshirib, sizga qaytaman" de — boshqa hech narsa
- E'tiroz aniqlanganida yangi qatorda yoz:
ETIROZ: [tur] | [mijozning aniq so'zlari] | [raqib nomi yoki "-"] | [raqib narxi yoki "-"]
  turlar: narx_baland / boshqa_joyda_arzon / hozir_kerak_emas / boshqa_servis_yaxshi / boshqa

"narx_baland" (qimmat, narx baland, boshqa yerda arzon):
- "Tushunaman. Biz 24/7 ishlaymiz, hujjatlar to'liq, yetkazish tezkor — sifat farqi bor." de
- Narxni muhokama qilma

"boshqa_joyda_arzon" (boshqa joyda arzonroq ko'rdim):
- AVVAL aniq so'ra: "Qaysi firmadan, qancha narxda?" — nom va raqam olguncha dalil berma
- Nom va narx olingach: "Ular [narx]da berishyapti — hujjat to'liqmi, kafolat bormi? Bizda hujjatlar rasmiylashtirilgan, yetkazish tezkor, ko'pchilik qaytib keladi."
- Mijoz qat'iy ketsa: "Mayli, kerak bo'lsa yozing — doim shu yerdamiz."
- ETIROZ: boshqa_joyda_arzon | [mijozning so'zlari] | [raqib nomi yoki "-"] | [raqib narxi yoki "-"]

"hozir_kerak_emas" (keyin, hozir yo'q, keyinroq):
- Bosim qilma: "Mayli, kerak bo'lganda yozing — doim shu yerda bo'lamiz." de

"boshqa_servis_yaxshi" (boshqa yerdan olaman, ular yaxshiroq):
- AVVAL aniq so'ra: "Qaysi firma? Ular nima bilan yaxshiroq — narxmi, yetkazishmi, munosabatmi?"
- Aniq javob olingach: "Tushunaman. Bizda [aniq ustunlik — sertifikat/tezkorlik/ishonch] bor — shu borada bir solishtirib ko'ramizmi?"
- Mijoz qat'iy ketsa: "Har kim o'z yo'lida. Kerak bo'lsa qaytib keling."
- ETIROZ: boshqa_servis_yaxshi | [mijozning so'zlari] | [raqib nomi yoki "-"] | -

BANK O'TKAZMA SO'RASA (mijoz "bank o'tkazma", "plastik", "karta", "o'tkazma" so'zlarini ishlatsa):
- "Aniqlab beraman" de, keyin yangi qatorda:
BANK_NARX_KUTILMOQDA: [marka] | [miqdor, yo'q bo'lsa "?"]

MUDDATLI TO'LOV SO'RASA:
- "Hozircha to'lov naqd yoki bank o'tkazma orqali amalga oshiriladi." de

KATTA MIQDOR (5 tonna va undan ko'p) SO'RASA:
- Narxni aytgandan so'ng: "Katta miqdor uchun qo'shimcha chegirma bo'lishi mumkin — aniqlayman." de

SIFAT HUJJATI YOKI SERTIFIKAT SO'RASA:
- "Ha, barcha mahsulotlarda sertifikat bor. Kerakli markani ayting, yuboray." de

MAHSULOT QACHON KELISHI SO'RASA:
- "Mavjud stokdan — 1-2 ish kuni ichida. Buyurtma bo'lsa — alohida aniqlayman." de

ALOQA RAQAMI SO'RALGANDA:
- FAQAT: "+998907080000 ga qo'ng'iroq qiling" de
- Boshqa hech qanday raqam yubormа — hatto telefon ko'rsatilgan bo'lsa ham

KOMPANIYA MA'LUMOTLARI (so'ralganda ayt):
- Kompaniya: Petro Plast
- Manzil: Toshkent, Eshonguzar ko'chasi (sklad)
- Ish vaqti: Dushanba-Shanba, 09:00-18:00
- Minimal buyurtma: 500 kg
- Yetkazib berish: manzil va miqdorga qarab kelishiladi
- Sifat sertifikati: barcha mahsulotlarda mavjud"""


# ── Yordamchi funksiyalar ──────────────────────────────────────────────────────

_MALE_NAMES = {
    "jasur", "jamshid", "bobur", "sardor", "sanjar", "sherzod", "shahzod",
    "ulugbek", "laziz", "otabek", "alisher", "nodir", "mansur", "rustam",
    "doston", "timur", "bahodir", "murod", "oybek", "diyorbek", "bekzod",
    "farrux", "islom", "eldor", "umid", "akbar", "kamol", "zafar", "davron",
    "firdavs", "jahongir", "abror", "mirzo", "dilshod", "hamza", "anvar",
    "nuriddin", "ilhom", "elmurod", "qodir", "tohir", "elbek", "komil",
    "muzaffar", "ibrohim", "ismoil", "yusuf", "abdulloh", "muhammad", "ahmad",
    "ali", "umar", "rahim", "nurbek", "ravshan", "shuhrat", "suxrob", "vohid",
    "xurshid", "hasan", "husayn", "kamoliddin", "javlon", "bahrom", "behruz",
    "bilol", "botirbek", "bunyod", "bunyodbek", "doniyor", "erkin", "farhod",
    "faridun", "fazliddin", "furqat", "hayot", "hikmat", "humoyun", "iskandar",
    "javohir", "lochin", "lochinbek", "mustafo", "nozim", "nurillo", "obid",
    "ortiq", "ozod", "rauf", "salim", "sarvar", "sarvarbek", "sirojiddin",
    "sobir", "sodiq", "sulton", "toxir", "ulmas", "uygun", "xasan", "yahyo",
    "yoqub", "zafar", "zafarjon", "zohid", "zubaydullo", "shamsiddin",
    "abdulaziz", "abdulhamid", "asliddin", "asror", "azamat", "azizbek",
    "baxtiyor", "doniybek", "eldorbek", "fattoh", "feruzbek", "husan",
    "husanboy", "husanjon", "islombek", "jabbor", "kenja", "muxammad",
    "nurullo", "ortiqboy", "otajon", "ravzaali", "sarvarjon", "sunnat",
    "tohirjon", "xasanboy", "xurshidbek", "zokirjon", "bekhzod", "bexruz",
}

_FEMALE_NAMES = {
    "nargiza", "malika", "zulfiya", "dilnoza", "feruza", "kamola", "ozoda",
    "maftuna", "nilufar", "shahnoza", "sarvinoz", "muazzam", "mohira",
    "lobar", "gulsanam", "barno", "nafisa", "aziza", "madina", "dilorom",
    "nasiba", "sabohat", "oydin", "rayhona", "kumush", "hulkar", "gulnora",
    "sevinch", "latofat", "manzura", "iroda", "farzona", "lola", "munira",
    "surayyo", "tabassum", "umida", "yulduz", "zuhra", "gavhar", "dildora",
    "hamida", "nozima", "qunduz", "sitora", "holida", "mavluda", "xurmo",
    "oysha", "robiya", "saodat", "sadoqat", "shahlo", "shirin", "soliha",
    "sultana", "xilola", "adolat", "bahora", "dilrabo", "dilfuza", "farida",
    "farangiz", "fotima", "gulbahor", "gulnoza", "hilola", "jamila",
    "kamolaxon", "komila", "malohat", "mashxura", "mavzuna", "mohichehra",
    "mohinur", "mukaddas", "nafosat", "nodira", "noila", "parizod",
    "ruxshona", "sevara", "shoira", "shohida", "tursunoy", "xadicha",
    "zamira", "zarnigor", "ziyoda", "nafosatxon", "mahliyo", "mohlaroyim",
}


def name_title(name: str) -> str:
    """O'zbek ismi bo'yicha 'aka' yoki 'opa' qo'shadi."""
    if not name:
        return name
    first = name.strip().split()[0].lower()
    if first in _MALE_NAMES:
        return f"{name} aka"
    if first in _FEMALE_NAMES:
        return f"{name} opa"
    return name


def _save_clients_json() -> None:
    try:
        with open(CLIENTS_DB_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in clients_db.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"JSON saqlashda xato: {e}")


def _load_clients_json() -> dict:
    try:
        with open(CLIENTS_DB_FILE, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"JSON yuklashda xato: {e}")
        return {}


# ── Qayta aloqa (follow-up) tizimi ───────────────────────────────────────────

def _load_follow_ups() -> list:
    try:
        with open(FOLLOW_UPS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"Follow-ups yuklashda xato: {e}")
        return []


def _save_follow_ups() -> None:
    try:
        with open(FOLLOW_UPS_FILE, "w", encoding="utf-8") as f:
            json.dump(follow_ups, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Follow-ups saqlashda xato: {e}")


def _load_prices() -> dict:
    try:
        with open(PRICES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Narxlar yuklashda xato: {e}")
        return {}


def _save_prices() -> None:
    try:
        with open(PRICES_FILE, "w", encoding="utf-8") as f:
            json.dump(current_prices, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Narxlar saqlashda xato: {e}")


def schedule_follow_up(ftype: str, customer_id: int, marka: str, days: int) -> None:
    now_ts = datetime.now(TASHKENT).timestamp()
    follow_ups.append({
        "type": ftype,
        "customer_id": customer_id,
        "marka": marka,
        "created_ts": now_ts,
        "send_ts": now_ts + days * 86400,
        "sent": False,
    })
    asyncio.create_task(asyncio.to_thread(_save_follow_ups))
    logger.info(f"Follow-up rejalashtirildi: {ftype} | cid={customer_id} | {days} kun")


# ── Google Sheets (mijozlar xotirasi) ─────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _get_sheets_token() -> str | None:
    if not _GC_RAW or not _CRYPTO_OK or not _SHEETS_ID:
        return None
    now = int(time.time())
    if _token_cache["token"] and now < _token_cache["exp"]:
        return _token_cache["token"]
    try:
        info = json.loads(_GC_RAW)
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    except Exception as e:
        logger.error(f"GOOGLE_CREDENTIALS parse xatosi: {e}")
        return None
    try:
        header  = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps({
            "iss": info["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now, "exp": now + 3600,
        }).encode())
        msg = f"{header}.{payload}".encode()
        pk  = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        sig = pk.sign(msg, _rsa_padding.PKCS1v15(), hashes.SHA256())
        jwt = f"{header}.{payload}.{_b64url(sig)}"
    except Exception as e:
        logger.error(f"JWT xatosi: {e}")
        return None
    try:
        resp  = _http.post("https://oauth2.googleapis.com/token",
                           data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                 "assertion": jwt}, timeout=10)
        token = resp.json().get("access_token")
        _token_cache.update({"token": token, "exp": now + 3000})
        return token
    except Exception as e:
        logger.error(f"Sheets token xatosi: {e}")
        return None


def _sheets_ensure_mijozlar(token: str) -> None:
    hdrs = {"Authorization": f"Bearer {token}"}
    meta = _http.get(_SHEETS_BASE, headers=hdrs, timeout=10)
    titles = [s["properties"]["title"] for s in meta.json().get("sheets", [])] if meta.ok else []
    if _MIJOZLAR_SHEET not in titles:
        _http.post(f"{_SHEETS_BASE}:batchUpdate", headers=hdrs, timeout=10,
                   json={"requests": [{"addSheet": {"properties": {"title": _MIJOZLAR_SHEET}}}]})
    rng = urllib.parse.quote(f"{_MIJOZLAR_SHEET}!A1:G1", safe="")
    _http.put(f"{_SHEETS_BASE}/values/{rng}", headers=hdrs, timeout=10,
              params={"valueInputOption": "RAW"},
              json={"values": [["chat_id", "name", "telegram", "sana", "til", "status", "lid"]]})


def sheets_load_clients() -> dict:
    token = _get_sheets_token()
    if not token:
        logger.warning("Sheets token yo'q — JSON fayldan yuklanadi.")
        return _load_clients_json()
    try:
        rng  = urllib.parse.quote(f"{_MIJOZLAR_SHEET}!A:G", safe="")
        r    = _http.get(f"{_SHEETS_BASE}/values/{rng}",
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if not r.ok:
            logger.error(f"Sheets yuklash {r.status_code} — JSON fallback.")
            return _load_clients_json()
        rows   = r.json().get("values", [])
        result = {}
        for row in rows[1:]:
            if not row:
                continue
            try:
                cid = int(row[0])
                result[cid] = {
                    "name":          row[1] if len(row) > 1 else "",
                    "telegram":      row[2] if len(row) > 2 else "",
                    "til":           row[4] if len(row) > 4 else "",
                    "had_issiq_lid": row[6] == "Ha" if len(row) > 6 else False,
                }
            except (ValueError, IndexError):
                continue
        logger.info(f"Sheets dan {len(result)} ta mijoz yuklandi.")
        return result
    except Exception as e:
        logger.error(f"Sheets yuklashda xato: {e}")
        return _load_clients_json()


def sheets_save_client(chat_id: int, data: dict) -> None:
    token = _get_sheets_token()
    if not token:
        _save_clients_json()
        return
    try:
        rng = urllib.parse.quote(f"{_MIJOZLAR_SHEET}!A:G", safe="")
        row = [
            str(chat_id),
            data.get("name", ""),
            data.get("telegram", ""),
            datetime.now(TASHKENT).strftime("%Y-%m-%d %H:%M"),
            data.get("til", ""),
            "Issiq lid" if data.get("had_issiq_lid") else "Yangi",
            "Ha" if data.get("had_issiq_lid") else "Yo'q",
        ]
        _http.post(f"{_SHEETS_BASE}/values/{rng}:append",
                   headers={"Authorization": f"Bearer {token}"},
                   params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                   json={"values": [row]}, timeout=10)
    except Exception as e:
        logger.error(f"Sheets saqlashda xato: {e}")
        _save_clients_json()


def detect_language(text: str) -> str:
    if not text:
        return ""
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    return "ru" if cyrillic / max(len(text), 1) > 0.3 else "uz"


def _sheets_ensure_lidlar(token: str) -> None:
    hdrs = {"Authorization": f"Bearer {token}"}
    try:
        meta = _http.get(_SHEETS_BASE, headers=hdrs, timeout=10)
        if not meta.ok:
            logger.error(f"Sheets meta xatosi: {meta.status_code} — {meta.text[:120]}")
            return
        titles = [s["properties"]["title"] for s in meta.json().get("sheets", [])]
        if _LIDLAR_SHEET not in titles:
            r = _http.post(
                f"{_SHEETS_BASE}:batchUpdate", headers=hdrs, timeout=10,
                json={"requests": [{"addSheet": {"properties": {"title": _LIDLAR_SHEET}}}]},
            )
            if r.ok:
                logger.info(f"Lidlar varag'i yaratildi.")
            else:
                logger.error(f"Lidlar varag'i yaratishda xato: {r.status_code} — {r.text[:120]}")
        else:
            logger.info("Lidlar varag'i mavjud.")
        rng = urllib.parse.quote(f"{_LIDLAR_SHEET}!A1:M1", safe="")
        r = _http.put(
            f"{_SHEETS_BASE}/values/{rng}", headers=hdrs, timeout=10,
            params={"valueInputOption": "RAW"},
            json={"values": [_LIDLAR_HEADER]},
        )
        if r.ok:
            logger.info(f"Lidlar header yozildi: {_LIDLAR_HEADER}")
        else:
            logger.error(f"Lidlar header xatosi: {r.status_code} — {r.text[:120]}")
    except Exception as e:
        logger.error(f"_sheets_ensure_lidlar xatosi: {e}")


def _lidlar_append(row: list) -> None:
    global _lidlar_init_done
    token = _get_sheets_token()
    if not token:
        logger.error("Lidlar append: token olinmadi")
        return
    if not _lidlar_init_done:
        _sheets_ensure_lidlar(token)
        _lidlar_init_done = True
    rng = urllib.parse.quote(f"{_LIDLAR_SHEET}!A:M", safe="")
    r = _http.post(
        f"{_SHEETS_BASE}/values/{rng}:append",
        headers={"Authorization": f"Bearer {token}"},
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"values": [row]},
        timeout=10,
    )
    if r.ok:
        logger.info(f"Lidlar append OK: {row[:3]}")
    else:
        logger.error(f"Lidlar append {r.status_code}: {r.text[:150]}")


def sheets_lidlar_customer(sender_id: int, data: dict, first_text: str = "") -> None:
    try:
        til = detect_language(first_text)
        row = [
            datetime.now(TASHKENT).strftime("%Y-%m-%d %H:%M"),
            data.get("name", ""), "", "", "", "", "", "",
            data.get("telegram", ""), "Yangi", til, "Birinchi aloqa", "",
        ]
        _lidlar_append(row)
        logger.info(f"Lidlar yangi mijoz: {data.get('name')} {data.get('telegram')}")
    except Exception as e:
        logger.error(f"sheets_lidlar_customer: {e}")


def sheets_lidlar_lead(sender_id: int, name: str, telegram: str, phone: str,
                       marka: str, miqdor: str, tolov: str, narx: str, til: str = "") -> None:
    try:
        row = [
            datetime.now(TASHKENT).strftime("%Y-%m-%d %H:%M"),
            name, phone, marka, miqdor, tolov, narx, "",
            telegram, "Issiq lid", til, "Buyurtma", "",
        ]
        _lidlar_append(row)
        logger.info(f"Lidlar lead: {name}, {marka}")
    except Exception as e:
        logger.error(f"sheets_lidlar_lead: {e}")


def sheets_lidlar_sovuq(name: str, phone: str) -> None:
    try:
        row = [
            datetime.now(TASHKENT).strftime("%Y-%m-%d %H:%M"),
            name, phone, "", "", "", "", "",
            "", "Sovuq", "", "Sovuq aloqa", "",
        ]
        _lidlar_append(row)
        logger.info(f"Lidlar sovuq lid: {name} {phone}")
    except Exception as e:
        logger.error(f"sheets_lidlar_sovuq: {e}")


def match_marka(stored: str, parsed: dict):
    """Marka nomini parsed dict da moslashtirib topadi (case-insensitive, prefix)."""
    if stored in parsed:
        return stored, parsed[stored]
    s = stored.strip().upper()
    for k, v in parsed.items():
        if k.strip().upper() == s:
            return k, v
    for k, v in parsed.items():
        kc = k.strip().upper()
        if s.startswith(kc) or kc.startswith(s):
            return k, v
    return None, None


_INTENT_SYSTEM = """\
Petro Plast savdo botining buyruq analizatorisin.
BOSS xabarini tahlil qilib, FAQAT quyidagi JSON formatida qaytaring (boshqa hech narsa yozma):
{"niyat": "<tur>", "parametrlar": {...}}

Niyat turlari:
- "narx_yangilash": {"marka": "<marka nomi>", "narx": <son>}
  Misol: "1561 narxi 16500 boldi" -> {"niyat":"narx_yangilash","parametrlar":{"marka":"1561","narx":16500}}
- "noma_lum": {}

Faqat narx yangilash niyatini aniqla. Boshqa har qanday xabar uchun "noma_lum" qaytaring."""


async def intent_router(text: str) -> dict:
    """Claude Haiku orqali BOSS xabaridan niyat aniqlanadi (qattiq format mos kelmasa fallback)."""
    def _sync_call():
        return claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system=_INTENT_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
    try:
        resp = await asyncio.to_thread(_sync_call)
        raw = resp.content[0].text.strip()
        logger.info(f"intent_router natija: {raw}")
        return json.loads(raw)
    except Exception as e:
        logger.error(f"intent_router xato: {e}")
        return {"niyat": "noma_lum", "parametrlar": {}}


def normalize_marka(raw: str) -> str:
    """So'z sinonimlari va sort naqshlarini qo'llab, matnni taqqoslash uchun normallashtiradi."""
    text = raw.strip().lower()
    text = re.sub(r'\(\s*1\s*\)', '(1sort)', text)
    text = re.sub(r'\(\s*2\s*\)', '(2sort)', text)
    text = re.sub(r'\b1\s*-?\s*sort\b', '(1sort)', text)
    text = re.sub(r'\b2\s*-?\s*sort\b', '(2sort)', text)
    words = text.split()
    words = [soz_sinonimlar_ro.get(w, w) for w in words]
    return " ".join(words)


async def resolve_marka(raw_marka: str) -> dict:
    """
    Marka nomini kanonik ro'yxatga moslashtiradi.
    Qaytaradi: {"status": "aniq"|"taklif"|"yangi", "kanonik": str|None}
    """
    norm = normalize_marka(raw_marka)

    for item in kanonik_markalar_ro:
        if normalize_marka(item["nom"]) == norm:
            return {"status": "aniq", "kanonik": item["nom"]}

    cached = alias_cache.get(norm)
    if cached:
        return {"status": "aniq", "kanonik": cached}
    if _DB_OK:
        db_alias = await asyncio.to_thread(_db.get_alias, norm)
        if db_alias:
            alias_cache[norm] = db_alias
            return {"status": "aniq", "kanonik": db_alias}

    nomlar_royxati = "\n".join(item["nom"] for item in kanonik_markalar_ro)
    system = f"""Quyidagi ro'yxatdan foydalanuvchi yozgan mahsulot nomiga ENG YAQIN mos kelgan nomni top.
Ro'yxat:
{nomlar_royxati}

Foydalanuvchi yozgan nom ro'yxatdagi biror nomga mos kelsa (imlo xatosi, qisqartma, so'z tartibi farqi bo'lsa ham), FAQAT shu JSON'ni qaytar:
{{"topildi": true, "kanonik": "<ro'yxatdagi aniq nom>"}}

Agar ro'yxatda mos keladigan HECH NARSA bo'lmasa (butunlay yangi mahsulot):
{{"topildi": false, "kanonik": null}}

Boshqa hech narsa yozma, faqat JSON."""
    try:
        def _sync_call():
            return claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                system=system,
                messages=[{"role": "user", "content": raw_marka}],
            )
        resp = await asyncio.to_thread(_sync_call)
        raw_json = resp.content[0].text.strip()
        result = json.loads(raw_json)
        if result.get("topildi") and result.get("kanonik"):
            return {"status": "taklif", "kanonik": result["kanonik"]}
        return {"status": "yangi", "kanonik": None}
    except Exception as e:
        logger.error(f"resolve_marka xato: {e}")
        return {"status": "yangi", "kanonik": None}


async def _ask_pending_item(event, item):
    narx = item["narx"]
    if item["status"] == "taklif":
        await event.respond(
            f"'{item['raw']}' — bu '{item['taklif_kanonik']}' emasmi? Tasdiqlasam, narxini {narx:,} ga o'rnataman. Ha/Yo'q".replace(",", " ")
        )
    else:
        await event.respond(
            f"'{item['raw']}' kanonik ro'yxatda yo'q. Yangi mahsulot sifatida qo'shib, narxini {narx:,} ga o'rnataymi? Ha/Yo'q".replace(",", " ")
        )


def parse_price_list(text: str) -> dict:
    prices = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # "0320 narx 16500" yoki "0320 narxi 16500" formati
        m = re.match(r'^(.+?)\s+narx[i]?\s+([\d\s,]+)$', line, re.IGNORECASE)
        if m:
            brand = m.group(1).strip()
            price_str = re.sub(r'[^\d]', '', m.group(2))
            if price_str and brand:
                prices[brand] = int(price_str)
                continue
        for sep in [" - ", ": ", "-", ":"]:
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2:
                    brand = parts[0].strip()
                    m2 = re.search(r'[\d][\d\s,]*', parts[1].strip())
                    if m2 and brand:
                        price_str = re.sub(r'[^\d]', '', m2.group())
                        if price_str:
                            prices[brand] = int(price_str)
                            break
    return prices


def get_prices_text() -> str:
    if not current_prices:
        return "Kiritilmagan"
    return "\n".join(f"{k}: {v:,} so'm/kg" for k, v in current_prices.items())


def extract_phone(text: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", text)
    m = re.search(r"\+?\d{9,13}", cleaned)
    return m.group() if m else ""


def has_valid_phone(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 9 and not all(c.isalpha() for c in text.strip())


TOLOV_KEY = "To’lov"

_INTERNAL_MARKERS = (
    "ISSIQ_LID", "ISM:", "SOHA:", "ETIROZ:", "NARX_KELISHUV:", "NARX_KUTILMOQDA:",
    "BANK_NARX_KUTILMOQDA:", "NOMA_LUM_MARKA:", "TEXNIK SAVOL:",
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
        elif upper.startswith("SOHA:"):
            _valid_sohalar = {
                "issiqxona_plyonkasi", "paket", "qop", "oyinchoq_idish",
                "quvur", "kabel", "bir_martalik_idish", "boshqa"
            }
            raw_soha = line.strip().split(":", 1)[1].strip().lower().replace(" ", "_")
            markers["soha"] = raw_soha if raw_soha in _valid_sohalar else "boshqa"
        elif upper.startswith("ETIROZ:"):
            value = line.strip().split(":", 1)[1].strip()
            parts = [p.strip() for p in value.split("|")]
            _valid_etirozlar = {
                "narx_baland", "boshqa_joyda_arzon",
                "hozir_kerak_emas", "boshqa_servis_yaxshi", "boshqa"
            }
            tur = parts[0].lower() if parts else "boshqa"
            raqib_nomi_raw  = parts[2] if len(parts) > 2 else "-"
            raqib_narxi_raw = parts[3] if len(parts) > 3 else "-"
            try:
                raqib_narxi_val: Optional[float] = (
                    float(raqib_narxi_raw.replace(" ", "").replace(",", "."))
                    if raqib_narxi_raw not in ("-", "", "?")
                    else None
                )
            except ValueError:
                raqib_narxi_val = None
            markers["etiroz"] = {
                "tur":         tur if tur in _valid_etirozlar else "boshqa",
                "matn":        parts[1] if len(parts) > 1 else "",
                "raqib_nomi":  raqib_nomi_raw  if raqib_nomi_raw  not in ("-", "", "?") else None,
                "raqib_narxi": raqib_narxi_val,
            }
        elif upper.startswith("NARX_KUTILMOQDA:"):
            value = line.strip().split(":", 1)[1].strip()
            parts = [p.strip() for p in value.split("|")]
            markalar = [m.strip() for m in parts[0].split(",") if m.strip()]
            miqdor = parts[1] if len(parts) > 1 else "?"
            markers["narx_kutilmoqda"] = [
                {"marka": m, "miqdor": miqdor} for m in (markalar or ["?"])
            ]
        elif upper.startswith("BANK_NARX_KUTILMOQDA:"):
            value = line.strip().split(":", 1)[1].strip()
            parts = [p.strip() for p in value.split("|")]
            markers["bank_narx_kutilmoqda"] = {
                "marka": parts[0] if parts else "?",
                "miqdor": parts[1] if len(parts) > 1 else "?",
            }
        elif upper.startswith("NARX_KELISHUV:"):
            value = line.strip().split(":", 1)[1].strip()
            parts = [p.strip() for p in value.split("|")]
            markers["narx_kelishuv"] = {
                "marka": parts[0] if parts else "?",
                "taklif": parts[1] if len(parts) > 1 else "?",
                "asl": parts[2] if len(parts) > 2 else "?",
            }
        elif any(upper.startswith(m.upper()) for m in _INTERNAL_MARKERS):
            pass
        else:
            customer_lines.append(line)
    return "\n".join(customer_lines).strip(), markers


def is_negative(text: str) -> bool:
    low = text.strip().lower()
    return low in {"yo'q", "yoq", "mumkin emas", "rad", "no", "bo'lmaydi", "bolmaydi"}


def extract_single_price(text: str) -> int | None:
    """BOSS ning qisqa narx javobini aniqlash (narx ro'yxatidan farqli)."""
    lines = [l for l in text.strip().split("\n") if l.strip()]
    if len(lines) > 3:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if 4 <= len(digits) <= 7:
        val = int(digits)
        if val >= 1000:
            return val
    return None


def build_lead_card(chat_id: int, phone: str, response_text: str, buyurtma_id: Optional[int] = None) -> str:
    c = clients_db.get(chat_id, {})
    details = {}
    for line in response_text.strip().split("\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            details[k.strip()] = v.strip()
    id_qator = f"Buyurtma ID: #{buyurtma_id}\n" if buyurtma_id else ""
    return (
        f"ISSIQ LID! (userbot)\n"
        f"{id_qator}"
        f"Ism: {c.get('name', '?')}\n"
        f"Telegram: {c.get('telegram', 'nomalum')}\n"
        f"Telefon: {phone}\n"
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

    def _call_claude():
        return claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=SYSTEM_PROMPT.format(prices=get_prices_text()),
            messages=conversations[chat_id],
        )

    try:
        resp = await asyncio.to_thread(_call_claude)
        msg = resp.content[0].text.strip()
        conversations[chat_id].append({"role": "assistant", "content": msg})
        logger.info(f"Claude javob ({chat_id}): {msg[:80]!r}")
        return msg
    except Exception as e:
        logger.error(f"Claude xatosi ({chat_id}): {e}")
        return "Bir daqiqa kuting."


# ── Event handlerlar ───────────────────────────────────────────────────────────

@client.on(events.NewMessage(incoming=True))
async def on_incoming_message(event):
    try:
        await _handle_message(event)
    except Exception as e:
        logger.error(f"Handler xatosi: {e}", exc_info=True)


async def _handle_message(event):
    sender_id = event.sender_id
    text = (event.message.text or "").strip()
    logger.info(f"Xabar keldi: sender={sender_id} is_private={event.is_private} text={text[:60]!r}")

    if not event.is_private:
        return

    # BOSS xabari
    if sender_id == BOSS_CHAT_ID:
        # BOSS kontakt yubordi — o'sha odamga birinchi yoz
        if isinstance(event.message.media, MessageMediaContact):
            contact = event.message.media
            target_id = contact.user_id
            if not target_id:
                await event.respond("Bu kontaktning Telegram ID si yo'q, xabar yuborib bo'lmadi.")
                return
            first_name = (contact.first_name or "").strip()
            if target_id not in clients_db:
                clients_db[target_id] = {"name": first_name, "telegram": ""}
                asyncio.create_task(asyncio.to_thread(sheets_save_client, target_id, clients_db[target_id]))
            titled = name_title(first_name) if first_name else ""
            greeting = (
                f"Salom{', ' + titled if titled else ''}! "
                f"Men Nargiza — Petro Plast kompaniyasining savdo menejeri. "
                f"Qanday yordam bera olaman?"
            )
            try:
                await client.send_message(target_id, greeting)
                await event.respond(f"Yuborildi: {first_name or target_id}")
                logger.info(f"Kontakt orqali yangi mijozga salomlashildi: {first_name} (id={target_id})")
            except Exception as e:
                await event.respond(f"Xabar yuborishda xato: {e}")
            return

        if not text:
            return

        # ── Pending intent tasdiq (Ha/Yo'q, navbat bilan) ──────────────────────
        pending = pending_intent.get(sender_id)
        if pending:
            age = time.time() - pending.get("ts", 0)
            low = text.strip().lower()
            if age > 300:
                pending_intent.pop(sender_id, None)
            elif low in ("ha", "ha.", "yes", "ok", "+"):
                queue = pending.get("queue", [])
                if queue:
                    item = queue.pop(0)
                    kanonik = item.get("taklif_kanonik") or item["raw"]
                    narx = item["narx"]
                    current_prices[kanonik] = narx
                    if _DB_OK:
                        asyncio.create_task(asyncio.to_thread(_db.upsert_narx, kanonik, narx))
                        asyncio.create_task(asyncio.to_thread(_db.upsert_alias, normalize_marka(item["raw"]), kanonik))
                        if item["status"] == "yangi":
                            asyncio.create_task(asyncio.to_thread(_db.insert_kanonik_marka, kanonik, "Boshqa"))
                            kanonik_markalar_ro.append({"nom": kanonik, "bolim": "Boshqa"})
                        alias_cache[normalize_marka(item["raw"])] = kanonik
                    else:
                        asyncio.create_task(asyncio.to_thread(_save_prices))
                    logger.info(f"Narx yangilandi (resolve_marka): {kanonik} = {narx} (dan: {item['raw']!r})")
                    await event.respond(f"{kanonik} narxi {narx:,} ga o'rnatildi.".replace(",", " "))
                if queue:
                    pending["queue"] = queue
                    pending["ts"] = time.time()
                    await _ask_pending_item(event, queue[0])
                else:
                    pending_intent.pop(sender_id, None)
                return
            elif low in ("yo'q", "yoq", "no", "bekor", "-"):
                queue = pending.get("queue", [])
                if queue:
                    queue.pop(0)
                if queue:
                    pending["queue"] = queue
                    pending["ts"] = time.time()
                    await event.respond("O'tkazib yuborildi.")
                    await _ask_pending_item(event, queue[0])
                else:
                    pending_intent.pop(sender_id, None)
                    await event.respond("Bekor qilindi.")
                return
            else:
                await event.respond("Iltimos, 'Ha' yoki 'Yo'q' deb javob bering (yoki 'bekor' deb yozing).")
                return

        # BOSS "+998901234567 Sardor aka" formatida sovuq lid yubordi
        cold_match = re.match(r'^(\+?\d{9,13})\s+(.+)$', text.strip())
        if cold_match:
            phone = cold_match.group(1).strip()
            name = cold_match.group(2).strip()
            asyncio.create_task(asyncio.to_thread(sheets_lidlar_sovuq, name, phone))
            now_ts = time.time()
            follow_ups.append({
                "type": "sovuq_lid",
                "phone": phone,
                "name": name,
                "created_ts": now_ts,
                "send_ts": now_ts + 3600,
                "sent": False,
            })
            asyncio.create_task(asyncio.to_thread(_save_follow_ups))
            await event.respond(f"Saqlandi! {name} ({phone}) — 1 soat ichida xabar yuboriladi.")
            return

        # "<id> sotildi [miqdor]" yoki "<id> sotilmadi"
        if _DB_OK:
            _sotildi_pat  = re.compile(
                r'^#?(\d+)\s+sotildi(?:\s+([\d.,]+)\s*(kg|tonna)?)?$', re.IGNORECASE
            )
            _sotilmadi_pat = re.compile(r'^#?(\d+)\s+sotilmadi(?:\s+(.+))?$', re.IGNORECASE)
            m_s  = _sotildi_pat.match(text.strip())
            m_sm = _sotilmadi_pat.match(text.strip())

            if m_s:
                buyurtma_id = int(m_s.group(1))
                miqdor_raw  = m_s.group(2)
                birlik      = (m_s.group(3) or "kg").lower()
                miqdor      = float(miqdor_raw.replace(",", ".")) if miqdor_raw else 0.0
                if birlik == "tonna":
                    miqdor *= 1000
                res = await asyncio.to_thread(_db.tasdiqla_buyurtma, buyurtma_id, miqdor)
                if res["ok"]:
                    await event.respond(f"Tasdiqlandi: #{buyurtma_id} {res['marka']} — sotildi.")
                elif res["sabab_xato"] == "topilmadi":
                    await event.respond(f"#{buyurtma_id} — bunday buyurtma topilmadi.")
                else:
                    await event.respond(f"#{buyurtma_id} allaqachon '{res.get('status', '?')}' holatida.")
                return

            if m_sm:
                buyurtma_id = int(m_sm.group(1))
                sabab = (m_sm.group(2) or "").strip()
                res = await asyncio.to_thread(_db.bekor_qil_buyurtma, buyurtma_id, sabab)
                if res["ok"]:
                    sabab_qismi = f" ({sabab})" if sabab else ""
                    await event.respond(f"Bekor qilindi: #{buyurtma_id} {res['marka']}{sabab_qismi}.")
                elif res["sabab_xato"] == "topilmadi":
                    await event.respond(f"#{buyurtma_id} — bunday buyurtma topilmadi.")
                else:
                    await event.respond(f"#{buyurtma_id} allaqachon '{res.get('status', '?')}' holatida.")
                return

        # /mijozlar — soha va status bo'yicha to'liq ro'yxat
        if text.strip().lower() == "/mijozlar":
            if not _DB_OK:
                await event.respond("Supabase ulanmagan, /mijozlar ishlamaydi.")
                return
            rows = await asyncio.to_thread(_db.get_boss_mijozlar)
            msg = _db.format_boss_mijozlar(rows)
            # Telegram xabar 4096 belgidan uzun bo'lmasligi uchun bo'laklarga ajratamiz
            for i in range(0, len(msg), 4000):
                await client.send_message(BOSS_CHAT_ID, msg[i:i + 4000])
            return

        # /yordam — barcha buyruqlar ro'yxati
        if text.strip().lower() == "/yordam":
            pending_names = ", ".join(
                clients_db[cid].get("name", str(cid))
                for cid in list(pending_price_requests) + list(pending_bank_requests)
            ) or "yo'q"
            await event.respond(
                "Buyruqlar:\n"
                "/yordam — shu ro'yxat\n"
                "/yoz [ism] [xabar] — mijozga xabar yuborish\n"
                "/mijozlar — soha va status bo'yicha to'liq ro'yxat\n\n"
                f"Narx kutayotganlar: {pending_names}\n"
                f"Jami mijozlar: {len(clients_db)}"
            )
            return

        # /yoz [ism] [xabar]
        if text.lower().startswith("/yoz "):
            parts = text[5:].strip().split(None, 1)
            if len(parts) < 2:
                await event.respond("Format: /yoz [ism] [xabar]\nMasalan: /yoz Jasur Kelasizmi bugun?")
                return
            search_name, message_body = parts[0].lower(), parts[1]
            found = [(cid, c) for cid, c in clients_db.items()
                     if c.get("name", "").lower().startswith(search_name)]
            if not found:
                await event.respond(f"'{parts[0]}' ismli mijoz topilmadi.")
                return
            customer_id, customer = found[-1]
            try:
                await client.send_message(customer_id, message_body)
                await event.respond(f"Yuborildi: {customer.get('name')} {customer.get('telegram', '')}")
            except Exception as e:
                await event.respond(f"Xato: {e}")
            return

        # Narx kelishuv — BOSS tasdiqlash yoki rad etish
        if pending_price_negotiations:
            low = text.strip().lower()
            if any(w in low for w in ["ha ", "ruxsat", "beramiz", "bo'ladi", "boladi", "ok", "roziman"]) or low in {"ha", "ok"}:
                price_digits = re.sub(r"[^\d\s]", " ", text)
                price_match = re.search(r"\d[\d ]*\d|\d{4,}", price_digits)
                customer_id, data = list(pending_price_negotiations.items())[-1]
                agreed_raw = re.sub(r"\s", "", price_match.group()) if price_match else data.get("taklif", "")
                marka = data.get("marka", "")
                try:
                    agreed_fmt = f"{int(agreed_raw):,}"
                    agreed_text = f"{marka} narxi {agreed_fmt} so'm/kg"
                except (ValueError, TypeError):
                    agreed_text = f"{marka} bo'yicha chegirma"
                await client.send_message(
                    customer_id,
                    f"Yaxshi xabar! {agreed_text} qabul qilindi. To'lov turini tasdiqlaysizmi?"
                )
                pending_price_negotiations.pop(customer_id, None)
                await event.respond(f"Mijozga yuborildi: {agreed_text}")
                return
            elif is_negative(text):
                customer_id, data = list(pending_price_negotiations.items())[-1]
                marka = data.get("marka", "")
                asl = data.get("asl", "")
                asl_part = f"{asl} so'm/kg — bu " if asl and asl != "?" else ""
                await client.send_message(
                    customer_id,
                    f"{marka} narxi {asl_part}yakuniy narximiz."
                )
                pending_price_negotiations.pop(customer_id, None)
                await event.respond("Mijozga yakuniy narx yuborildi.")
                return

        # "yo'q" — bank o'tkazma rad etildi
        if is_negative(text) and pending_bank_requests:
            customer_id, req = list(pending_bank_requests.items())[-1]
            pending_bank_requests.pop(customer_id)
            c = clients_db.get(customer_id, {})
            await client.send_message(
                customer_id,
                "Hozirda faqat naqd to'lov qabul qilamiz."
            )
            logger.info(f"Bank rad: {c.get('name', customer_id)} ga xabar yuborildi.")
            return

        parsed_prices = parse_price_list(text)

        # Bank so'rovlari — brand mos kelsa yuborish
        bank_notified = []
        for customer_id, req in list(pending_bank_requests.items()):
            matched_key, price = match_marka(req["marka"], parsed_prices)
            if price is None:
                continue
            c = clients_db.get(customer_id, {})
            titled = name_title(c.get("name", ""))
            miqdor = req["miqdor"]
            prefix = f"{titled}, yaxshi xabar! " if titled else "Yaxshi xabar! "
            msg = f"{prefix}{matched_key} bank o'tkazma narxi: {price:,} so'm/kg."
            if miqdor and miqdor != "?":
                msg += f"\n{miqdor} uchun buyurtmani tasdiqlaysizmi?"
            else:
                msg += "\nBuyurtmani tasdiqlaysizmi?"
            try:
                await client.send_message(customer_id, msg)
                pending_bank_requests.pop(customer_id)
                bank_notified.append(customer_id)
                schedule_follow_up("narx_javob_yoq", customer_id, matched_key, 3)
                logger.info(f"Bank narx yuborildi: {titled} — {matched_key} = {price:,}")
            except Exception as e:
                logger.error(f"Bank narx yuborishda xato ({customer_id}): {e}")

        # Faqat raqam yuborganda — oxirgi bank so'ragan mijozga (priority)
        if not bank_notified and pending_bank_requests:
            single = extract_single_price(text)
            if single is not None:
                customer_id, req = list(pending_bank_requests.items())[-1]
                c = clients_db.get(customer_id, {})
                titled = name_title(c.get("name", ""))
                miqdor = req["miqdor"]
                prefix = f"{titled}, yaxshi xabar! " if titled else "Yaxshi xabar! "
                msg = f"{prefix}{req['marka']} bank o'tkazma narxi: {single:,} so'm/kg."
                if miqdor and miqdor != "?":
                    msg += f"\n{miqdor} uchun buyurtmani tasdiqlaysizmi?"
                else:
                    msg += "\nBuyurtmani tasdiqlaysizmi?"
                try:
                    await client.send_message(customer_id, msg)
                    pending_bank_requests.pop(customer_id)
                    schedule_follow_up("narx_javob_yoq", customer_id, req["marka"], 3)
                    logger.info(f"Bank narx (single) yuborildi: {titled} = {single:,}")
                except Exception as e:
                    logger.error(f"Bank narx yuborishda xato ({customer_id}): {e}")

        # Naqd narx so'rovlari — har bir mijozning barcha brendlari bo'yicha tekshirish
        notified = []
        for customer_id, req_list in list(pending_price_requests.items()):
            c = clients_db.get(customer_id, {})
            titled = name_title(c.get("name", ""))
            answered = []
            for req in req_list:
                matched_key, price = match_marka(req["marka"], parsed_prices)
                if price is None:
                    continue
                miqdor = req["miqdor"]
                prefix = f"{titled}, yaxshi xabar! " if titled else "Yaxshi xabar! "
                msg = f"{prefix}{matched_key} narxi: {price:,} so'm/kg."
                if miqdor and miqdor != "?":
                    msg += f"\n{miqdor} uchun buyurtmani tasdiqlaysizmi?"
                else:
                    msg += "\nBuyurtmani tasdiqlaysizmi?"
                try:
                    await client.send_message(customer_id, msg)
                    answered.append(req)
                    notified.append(f"{titled or customer_id} ({matched_key})")
                    schedule_follow_up("narx_javob_yoq", customer_id, matched_key, 3)
                    logger.info(f"Narx yuborildi: {titled} — {matched_key} = {price:,}")
                except Exception as e:
                    logger.error(f"Narx yuborishda xato ({customer_id}): {e}")
            for r in answered:
                req_list.remove(r)
            if not req_list:
                pending_price_requests.pop(customer_id)

        # Faqat raqam yuborganda — oxirgi naqd so'ragan mijozning oxirgi brendiga
        if not notified and pending_price_requests:
            single = extract_single_price(text)
            if single is not None:
                customer_id, req_list = list(pending_price_requests.items())[-1]
                c = clients_db.get(customer_id, {})
                titled = name_title(c.get("name", ""))
                req = req_list[-1]
                marka, miqdor = req["marka"], req["miqdor"]
                prefix = f"{titled}, yaxshi xabar! " if titled else "Yaxshi xabar! "
                msg = f"{prefix}{marka} narxi: {single:,} so'm/kg."
                if miqdor and miqdor != "?":
                    msg += f"\n{miqdor} uchun buyurtmani tasdiqlaysizmi?"
                else:
                    msg += "\nBuyurtmani tasdiqlaysizmi?"
                try:
                    await client.send_message(customer_id, msg)
                    req_list.remove(req)
                    if not req_list:
                        pending_price_requests.pop(customer_id)
                    schedule_follow_up("narx_javob_yoq", customer_id, marka, 3)
                    logger.info(f"Narx (single) yuborildi: {titled} — {marka} = {single:,}")
                except Exception as e:
                    logger.error(f"Narx yuborishda xato ({customer_id}): {e}")

        items_to_resolve = []
        if parsed_prices:
            items_to_resolve = list(parsed_prices.items())
        else:
            result = await intent_router(text)
            if result.get("niyat") == "narx_yangilash":
                p = result.get("parametrlar", {})
                marka = str(p.get("marka", "")).strip()
                try:
                    narx = int(p.get("narx", 0))
                except (TypeError, ValueError):
                    narx = 0
                if marka and narx > 0:
                    items_to_resolve = [(marka, narx)]

        if items_to_resolve:
            auto_saved = []
            queue = []
            for raw_marka, narx in items_to_resolve:
                resolved = await resolve_marka(raw_marka)
                if resolved["status"] == "aniq":
                    kanonik = resolved["kanonik"]
                    current_prices[kanonik] = narx
                    if _DB_OK:
                        asyncio.create_task(asyncio.to_thread(_db.upsert_narx, kanonik, narx))
                    else:
                        asyncio.create_task(asyncio.to_thread(_save_prices))
                    auto_saved.append(f"{kanonik}: {narx:,}".replace(",", " "))
                else:
                    queue.append({
                        "raw": raw_marka,
                        "narx": narx,
                        "status": resolved["status"],
                        "taklif_kanonik": resolved.get("kanonik"),
                    })

            if auto_saved:
                await event.respond("Narxlar saqlandi:\n" + "\n".join(auto_saved))
                logger.info(f"Narxlar avtomatik saqlandi: {auto_saved}")

            if queue:
                pending_intent[sender_id] = {"queue": queue, "ts": time.time()}
                await _ask_pending_item(event, queue[0])

        notified_str = ", ".join(notified) if notified else "yo'q"
        logger.info(f"BOSS narx xabari. Yangi: {len(parsed_prices)} ta. Xabardor: {notified_str}")
        return

    # Mijoz xabari
    if not text:
        return

    if sender_id not in clients_db:
        sender = await event.get_sender()
        username = f"@{sender.username}" if getattr(sender, "username", None) else ""

        # Supabase dan qaytgan mijozni tekshirish
        qaytgan = None
        oxirgi_buyurtma = None
        if _DB_OK:
            qaytgan = await asyncio.to_thread(_db.get_mijoz, sender_id)
            if qaytgan:
                oxirgi_buyurtma = await asyncio.to_thread(_db.get_oxirgi_buyurtma, sender_id)

        if qaytgan:
            # Qaytgan mijoz — keshga yuklaymiz, ism/soha so'rash o'tkazib yuboriladi
            clients_db[sender_id] = {
                "name": qaytgan.get("ism") or getattr(sender, "first_name", "") or "",
                "telegram": username,
                "til": qaytgan.get("til") or detect_language(text),
                "soha": qaytgan.get("soha") or "",
                "had_issiq_lid": bool(oxirgi_buyurtma),
                "last_marka": oxirgi_buyurtma["marka"] if oxirgi_buyurtma else "",
            }
            # Salomlashuv xabarini AI ga kontekst sifatida beramiz
            ism = clients_db[sender_id]["name"]
            ism_titled = name_title(ism) if ism else ""
            if oxirgi_buyurtma:
                marka = oxirgi_buyurtma["marka"]
                salom_ctx = (
                    f"[TIZIM: Bu qaytgan mijoz. Ism: {ism_titled or ism}. "
                    f"Oxirgi buyurtma: {marka}. "
                    f"Birinchi xabarda: 'Salom, {ism_titled or ism}! "
                    f"Oxirgi marta {marka} olgan edingiz, yana shundan kerakmi yoki boshqa narsa?' de. "
                    f"Ism va soha so'rama — allaqachon ma'lum.]"
                )
            else:
                salom_ctx = (
                    f"[TIZIM: Bu qaytgan mijoz. Ism: {ism_titled or ism}. "
                    f"Birinchi xabarda: 'Salom, {ism_titled or ism}! Nima kerak?' de. "
                    f"Ism va soha so'rama — allaqachon ma'lum.]"
                )
            if sender_id not in conversations:
                conversations[sender_id] = []
            conversations[sender_id].insert(0, {"role": "user", "content": salom_ctx})
            logger.info(f"Qaytgan mijoz: {clients_db[sender_id]['name']} {username}")
        else:
            # Yangi mijoz — standart oqim
            clients_db[sender_id] = {
                "name": getattr(sender, "first_name", "") or "",
                "telegram": username,
                "til": detect_language(text),
            }
            asyncio.create_task(asyncio.to_thread(sheets_save_client, sender_id, clients_db[sender_id]))
            asyncio.create_task(asyncio.to_thread(
                sheets_lidlar_customer, sender_id, clients_db[sender_id], text
            ))
            if _DB_OK:
                asyncio.create_task(asyncio.to_thread(
                    _db.upsert_mijoz,
                    sender_id,
                    ism=clients_db[sender_id].get("name", ""),
                    telegram_username=username,
                    til=clients_db[sender_id].get("til", ""),
                ))
            logger.info(f"Yangi mijoz: {clients_db[sender_id]['name']} {username}")

    clients_db[sender_id]["last_msg_ts"] = datetime.now(TASHKENT).timestamp()

    response = await get_ai_response(sender_id, text)
    customer_text, markers = parse_response(response)

    if "ism" in markers:
        clients_db[sender_id]["name"] = markers["ism"]
        asyncio.create_task(asyncio.to_thread(sheets_save_client, sender_id, clients_db[sender_id]))
        if _DB_OK:
            asyncio.create_task(asyncio.to_thread(_db.update_mijoz_ism, sender_id, markers["ism"]))

    if "soha" in markers:
        clients_db[sender_id]["soha"] = markers["soha"]
        if _DB_OK:
            asyncio.create_task(asyncio.to_thread(
                _db.upsert_mijoz, sender_id,
                soha=markers["soha"], soha_aniqlangan_avtomatik=True
            ))

    if "etiroz" in markers and _DB_OK:
        e = markers["etiroz"]
        await asyncio.to_thread(
            _db.add_etiroz,
            sender_id,
            e["tur"],
            e["matn"],
            clients_db.get(sender_id, {}).get("last_buyurtma_id"),
            e.get("raqib_nomi"),
            e.get("raqib_narxi"),
        )
        if e.get("raqib_nomi"):
            mijoz     = clients_db.get(sender_id, {})
            ism       = mijoz.get("name") or str(sender_id)
            tel       = mijoz.get("telefon") or "?"
            marka     = mijoz.get("last_marka") or "?"
            narx_qism = (
                f"{int(e['raqib_narxi']):,} so'm".replace(",", " ")
                if e.get("raqib_narxi") else "noma'lum"
            )
            alert = (
                f"⚠️ RAQOBATCHI ANIQLANDI\n"
                f"Mijoz: {ism} ({tel})\n"
                f"Marka: {marka}\n"
                f"Raqobatchi: {e['raqib_nomi']} — {narx_qism}\n"
                f"Mijoz so'zi: {e['matn']}"
            )
            try:
                await client.send_message(BOSS_CHAT_ID, alert)
            except Exception as exc:
                logger.error(f"Raqobatchi ogohlantirish xato: {exc}")

    if "issiq_lid" in markers:
        phone = extract_phone(text) if has_valid_phone(text) else "?"
        await event.respond("Rahmat, tez orada bog'lanamiz.")
        # Marka va detallarni ajratib olish
        lid_details = {}
        for line in response.strip().split("\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                lid_details[k.strip()] = v.strip()
        lid_marka = lid_details.get("Marka", "mahsulot")
        clients_db[sender_id]["had_issiq_lid"] = True
        clients_db[sender_id]["last_marka"] = lid_marka
        schedule_follow_up("issiq_lid", sender_id, lid_marka, 4)
        # Avval Supabase ga yozib ID olamiz, keyin kartochkaga qo'shamiz
        # Telefon: avval ISSIQ_LID blokidan, bo'lmasa so'nggi xabardan
        telefon_db = lid_details.get("Telefon", "").strip()
        if not telefon_db and phone != "?":
            telefon_db = phone
        if telefon_db:
            clients_db.setdefault(sender_id, {})["telefon"] = telefon_db
        buyurtma_id = None
        if _DB_OK:
            buyurtma_id = await asyncio.to_thread(
                _db.upsert_mijoz_va_buyurtma,
                sender_id,
                clients_db[sender_id].get("name", ""),
                telefon_db,
                clients_db[sender_id].get("telegram", ""),
                clients_db[sender_id].get("til", ""),
                lid_marka,
                lid_details.get("Miqdor", "?"),
                clients_db[sender_id].get("soha"),
            )
        if buyurtma_id:
            clients_db[sender_id]["last_buyurtma_id"] = buyurtma_id
        card = build_lead_card(sender_id, phone, response, buyurtma_id)
        await client.send_message(BOSS_CHAT_ID, card)
        logger.info(f"Issiq lid BOSS ga yuborildi: {clients_db[sender_id].get('name', sender_id)} tel={phone} buyurtma_id={buyurtma_id}")
        asyncio.create_task(asyncio.to_thread(
            sheets_lidlar_lead,
            sender_id,
            clients_db[sender_id].get("name", ""),
            clients_db[sender_id].get("telegram", ""),
            phone,
            lid_marka,
            lid_details.get("Miqdor", ""),
            lid_details.get("To'lov", ""),
            lid_details.get("Narx", ""),
            clients_db[sender_id].get("til", ""),
        ))
        if conversations.get(sender_id):
            conversations[sender_id][-1]["content"] = "Rahmat, tez orada bog'lanamiz."
        return

    if "narx_kutilmoqda" in markers:
        req_list = markers["narx_kutilmoqda"]
        pending_price_requests[sender_id] = req_list
        titled = name_title(clients_db[sender_id].get("name", ""))
        markalar_str = ", ".join(r["marka"] for r in req_list)
        miqdor = req_list[0]["miqdor"] if req_list else "?"
        miqdor_part = f" ({miqdor})" if miqdor != "?" else ""
        boss_msg = f"{titled} {markalar_str}{miqdor_part} narxini so'rayapti, qancha?"
        await client.send_message(BOSS_CHAT_ID, boss_msg)
        logger.info(f"Narx so'rovi BOSS ga: {boss_msg}")
        if req_list:
            clients_db[sender_id]["last_marka"] = req_list[0]["marka"]

    if "bank_narx_kutilmoqda" in markers:
        req = markers["bank_narx_kutilmoqda"]
        pending_bank_requests[sender_id] = req
        name = clients_db[sender_id].get("name", "")
        miqdor_part = f"dan {req['miqdor']}" if req["miqdor"] != "?" else ""
        boss_msg = f"{name} bank o'tkazma{miqdor_part} so'rayapti, {req['marka']} narx qancha?"
        await client.send_message(BOSS_CHAT_ID, boss_msg)
        logger.info(f"Bank narx so'rovi BOSS ga yuborildi: {boss_msg}")

    if "narx_kelishuv" in markers:
        data = markers["narx_kelishuv"]
        pending_price_negotiations[sender_id] = data
        titled = name_title(clients_db[sender_id].get("name", ""))
        marka = data["marka"]
        taklif = data["taklif"]
        if taklif and taklif != "?":
            boss_msg = f"{titled} chegirma so'rayapti — {marka} {taklif} so'm/kg qilib bersak bo'ladimi?"
        else:
            boss_msg = f"{titled} chegirma so'rayapti — {marka} bo'yicha chegirma beramizmi?"
        await client.send_message(BOSS_CHAT_ID, boss_msg)
        logger.info(f"Narx kelishuv BOSS ga: {boss_msg}")

    if customer_text:
        logger.info(f"Mijozga javob yuborilmoqda ({sender_id}): {customer_text[:80]!r}")
        await event.respond(customer_text)
        logger.info(f"Javob yuborildi ({sender_id})")
    else:
        logger.warning(f"customer_text bo'sh ({sender_id}), javob yuborilmadi. raw={response[:100]!r}")


# ── Guruh e'lonlari ────────────────────────────────────────────────────────────

_ELON_SHABLON = """\
Polietilen

0220 - {0220}
0320 - {0320}
0525 - {0525}

22b02 - {22b02}
2102 repack - {2102 repack}
2102 original - {2102 original}
2119 original - {2119 original}
2119 xitoy - {2119 xitoy}
0209 xitoy - {0209 xitoy}

Sibur 30200 - {Sibur 30200}
Sibur 153 - {Sibur 153}

1561 - {1561}
1561 (2sort) - {1561 (2sort)}
0760 - {0760}
52518 repack - {52518 repack}
J2210 - {J2210}
J2200 - {J2200}
2560 - {2560}

Ilam 7000 original - {Ilam 7000 original}
5100 (xitoy 7000) - {5100 (xitoy 7000)}

342 - {342}
Py456 - {Py456}
Pe100 Jam repack - {Pe100 Jam repack}
Pe100 Jam original - {Pe100 Jam original}

Bl3 repack - {Bl3 repack}
By460 - {By460}
By456 - {By456}

Polipropilen

J150 - {J150}
J160 - {J160}
J350 - {J350}
J360 - {J360}
J550 - {J550}
J560 - {J560}
J570 - {J570}

Jm370 - {Jm370}
Jm375 - {Jm375}

Y130 - {Y130}
Fo130 - {Fo130}
Sibur 030 - {Sibur 030}
Xitoy 1003 🔴 - {Xitoy 1003 🔴}
Xitoy 1003 🔵 - {Xitoy 1003 🔵}
Fr170 - {Fr170}

Sibx ppr 003 - {Sibx ppr 003}
Ppr 4401 xitoy - {Ppr 4401 xitoy}
Ppr 200 - {Ppr 200}

Gpps 1551 - {Gpps 1551}
Gpps 1551 repack - {Gpps 1551 repack}
Gpps 500 xitoy - {Gpps 500 xitoy}

Abs 121 LG - {Abs 121 LG}
Abs GP 35 - {Abs GP 35}
Abs Kunlun Xitoy - {Abs Kunlun Xitoy}

Polistirol 7420 - {Polistirol 7420}
Polistirol 1540 - {Polistirol 1540}
Polistirol 4512 - {Polistirol 4512}

Pvx Xitoy - {Pvx Xitoy}
Pvx Navoiy azot sg3 - {Pvx Navoiy azot sg3}
Pvx Navoiy azot sg5 - {Pvx Navoiy azot sg5}"""


def build_elon_text() -> str:
    lines_out = []
    for raw_line in _ELON_SHABLON.splitlines():
        brace_start = raw_line.find("{")
        brace_end   = raw_line.find("}")
        if brace_start != -1 and brace_end != -1:
            key  = raw_line[brace_start + 1:brace_end]
            narx = current_prices.get(key) or current_prices.get(key.lower())
            if narx is None:
                continue
            lines_out.append(raw_line[:brace_start] + str(int(narx)))
        else:
            lines_out.append(raw_line)
    result, prev_blank = [], False
    for ln in lines_out:
        is_blank = ln.strip() == ""
        if is_blank and prev_blank:
            continue
        result.append(ln)
        prev_blank = is_blank
    return "\n".join(result).strip()


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
    if not current_prices:
        logger.info("Hali narx kiritilmagan, e'lon yuborilmadi.")
        return
    price_text = build_elon_text()
    if not price_text:
        logger.info("Narxlar to'ldirilmagan, e'lon yuborilmadi.")
        return
    groups = await get_all_groups()
    if not groups:
        logger.warning("Hech qanday guruh topilmadi.")
        return
    ok, fail = 0, 0
    for dialog in groups:
        try:
            msg = price_text + "\n\n📞 +998907080000\n✈️ @nargiza_petroplast"
            await client.send_message(dialog.id, msg)
            logger.info(f"  [OK] {dialog.name}")
            ok += 1
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"  [XATO] {dialog.name}: {e}")
            fail += 1
    logger.info(f"E'lon natijasi: {ok} yuborildi, {fail} xato.")


async def follow_up_checker():
    await asyncio.sleep(60)
    while True:
        now = datetime.now(TASHKENT)
        now_ts = now.timestamp()
        # Faqat ish vaqtida yuborish
        if not (9 <= now.hour < 18):
            await asyncio.sleep(3600)
            continue

        changed = False

        # Type 1 (narx_javob_yoq) va Type 2 (issiq_lid): rejalashtirilgan follow-uplar
        for fu in follow_ups:
            if fu["sent"] or now_ts < fu["send_ts"]:
                continue

            # Sovuq lid — telefon raqamiga birinchi xabar
            if fu["type"] == "sovuq_lid":
                phone = fu.get("phone", "")
                name = fu.get("name", "")
                if not phone:
                    fu["sent"] = True
                    changed = True
                    continue
                msg = (
                    f"Salom {name}! Men Nargiza — Petro Plast savdo menejerimiz. "
                    f"Polietilen yoki polipropilen kerak bo'lsa, eng yaxshi narxni beramiz. "
                    f"Hozir qaysi marka bilan ishlayapsiz?"
                )
                try:
                    await client.send_message(phone, msg)
                    fu["sent"] = True
                    changed = True
                    logger.info(f"Sovuq lid xabar yuborildi: {name} ({phone})")
                    await client.send_message(BOSS_CHAT_ID, f"Sovuq lid: {name} ({phone}) ga xabar yuborildi.")
                except Exception as e:
                    logger.error(f"Sovuq lid xato ({phone}): {e}")
                    await client.send_message(BOSS_CHAT_ID, f"Sovuq lid xatosi: {name} ({phone}) — {e}")
                continue

            customer_id = fu["customer_id"]
            c = clients_db.get(customer_id, {})
            # Mijoz scheduled dan keyin javob bergan bo'lsa — o'tkazib yuborish
            if c.get("last_msg_ts", 0) > fu["created_ts"]:
                fu["sent"] = True
                changed = True
                logger.info(f"Follow-up o'tkazildi (mijoz javob berdi): {fu['type']} | {customer_id}")
                continue
            titled = name_title(c.get("name", "")) or "Salom"
            marka = fu["marka"]
            if fu["type"] == "narx_javob_yoq":
                msg = f"{titled}, salom! O'sha {marka} bo'yicha so'ragan edingiz — hozir ham kerak bo'lsa, ayting?"
            elif fu["type"] == "issiq_lid":
                msg = f"{titled}, salom! {marka} bo'yicha gaplashgan edik — qaror qildingizmi?"
            else:
                continue
            try:
                await client.send_message(customer_id, msg)
                fu["sent"] = True
                changed = True
                logger.info(f"Follow-up yuborildi: {fu['type']} | {customer_id} | {marka}")
            except Exception as e:
                logger.error(f"Follow-up xato ({customer_id}): {e}")

        # Type 3: doimiy mijoz 10 kun javob yo'q
        for customer_id, c in list(clients_db.items()):
            if not c.get("had_issiq_lid"):
                continue
            last_ts = c.get("last_msg_ts", 0)
            if not last_ts or (now_ts - last_ts) < 10 * 86400:
                continue
            # Oxirgi type-3 yuborilganidan 20 kun o'tmagan bo'lsa o'tkazib yuborish
            if (now_ts - c.get("last_f3_ts", 0)) < 20 * 86400:
                continue
            marka = c.get("last_marka", "mahsulot")
            titled = name_title(c.get("name", "")) or "Salom"
            msg = f"{titled}, salom! {marka} qoldiqlari tugab qolmadimi? Yangi partiya bor."
            try:
                await client.send_message(customer_id, msg)
                clients_db[customer_id]["last_f3_ts"] = now_ts
                changed = True
                logger.info(f"Doimiy mijoz follow-up: {customer_id} | {marka}")
            except Exception as e:
                logger.error(f"Doimiy mijoz follow-up xato ({customer_id}): {e}")

        if changed:
            asyncio.create_task(asyncio.to_thread(_save_follow_ups))

        # Kunlik 20:00 hisoboti — kutilmoqdalar + bugungi sotuv
        if _DB_OK and now.hour == 20:
            hisobot_key = f"hisobot_{now.date()}"
            if not _hisobot_store.get(hisobot_key):
                _hisobot_store[hisobot_key] = True

                # Xabar 1: kutilmoqda buyurtmalar
                rows = await asyncio.to_thread(_db.get_kutilmoqda_buyurtmalar)
                if rows:
                    lines = ["Kutilmoqda buyurtmalar:\n"]
                    for r in rows:
                        mijoz  = r.get("mijozlar") or {}
                        ism    = mijoz.get("ism") or str(r["mijoz_id"])
                        tel    = mijoz.get("telefon") or "—"
                        miqdor = f"{r['miqdor']} {r['birlik']}" if r.get("miqdor") else "?"
                        lines.append(f"#{r['id']} | {ism} ({tel}) | {r['marka']} | {miqdor}")
                    lines.append("\nFormat: '42 sotildi 500' yoki '42 sotilmadi narx baland'")
                    try:
                        await client.send_message(BOSS_CHAT_ID, "\n".join(lines))
                    except Exception as e:
                        logger.error(f"20:00 kutilmoqda xabar xato: {e}")

                # Xabar 2: bugungi sotuv hisoboti + e'tirozlar
                sotuv = await asyncio.to_thread(_db.get_bugungi_sotuv)
                if sotuv:
                    lines = [f"Bugungi sotuv ({now.strftime('%d.%m.%Y')}):\n"]
                    jami = 0
                    for r in sotuv:
                        kg = r.get("jami_kg") or 0
                        jami += kg
                        lines.append(
                            f"{r['marka']}: {int(kg):,} kg ({r['buyurtmalar_soni']} ta)".replace(",", " ")
                        )
                    lines.append(f"\nJami: {int(jami):,} kg".replace(",", " "))
                    if _DB_OK:
                        etirozlar = await asyncio.to_thread(_db.get_etirozlar_taqsimoti)
                        bugun_etirozlar = [r for r in etirozlar if r.get("bugungi", 0)]
                        if bugun_etirozlar:
                            lines.append("\nE'tirozlar (bugun):")
                            for r in bugun_etirozlar:
                                lines.append(f"  {r['etiroz_turi']}: {r['bugungi']} ta")
                    try:
                        await client.send_message(BOSS_CHAT_ID, "\n".join(lines))
                    except Exception as e:
                        logger.error(f"20:00 sotuv hisoboti xato: {e}")

        await asyncio.sleep(3600)


def _report_status(c: dict, now_ts: float) -> str | None:
    """Mijoz holati: daily va weekly hisobot uchun."""
    last_ts = c.get("last_msg_ts", 0)
    if not last_ts:
        return None  # hech qachon yozmagan — hisobotda ko'rsatilmaydi
    days = (now_ts - last_ts) / 86400
    if c.get("last_f3_ts"):
        return "Doimiy"
    if c.get("had_issiq_lid"):
        return "Issiq" if days < 14 else "1-sotuv"
    if days > 7:
        return "Uxlab qoldi"
    return "Yangi"


def _report_note(c: dict, now_ts: float) -> str:
    last_ts = c.get("last_msg_ts", 0)
    if not last_ts:
        return ""
    days = int((now_ts - last_ts) / 86400)
    if days == 0:
        return "Bugun"
    if days == 1:
        return "Kecha"
    return f"{days} kun avval"


async def reporter():
    """Har kuni 09:00 da kunlik, Dushanba — haftalik hisobot BOSS ga."""
    while True:
        now = datetime.now(TASHKENT)
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        now = datetime.now(TASHKENT)
        now_ts = now.timestamp()

        # ── Kunlik: aktiv mijozlar kartochkasi ──
        cards = []
        for cid, c in list(clients_db.items()):
            status = _report_status(c, now_ts)
            if status is None:
                continue
            last_ts = c.get("last_msg_ts", 0)
            if (now_ts - last_ts) > 30 * 86400:
                continue  # 30 kundan eski — kunlik hisobotda ko'rsatilmaydi
            ism = c.get("name") or str(cid)
            marka = c.get("last_marka", "—")
            izoh = _report_note(c, now_ts)
            cards.append(f"{ism} | {status} | {marka} | {izoh}")

        if cards:
            header = f"Kunlik hisobot ({now.strftime('%d.%m.%Y')}):\n"
            body = "\n".join(f"{i+1}- {line}" for i, line in enumerate(cards))
            try:
                await client.send_message(BOSS_CHAT_ID, header + body)
            except Exception as e:
                logger.error(f"Kunlik hisobot xato: {e}")
        else:
            try:
                await client.send_message(
                    BOSS_CHAT_ID,
                    f"Kunlik hisobot ({now.strftime('%d.%m.%Y')}): Aktiv mijoz yo'q."
                )
            except Exception as e:
                logger.error(f"Kunlik hisobot xato: {e}")

        # ── Haftalik: Dushanba ──
        if now.weekday() == 0:
            counts = {"Yangi": 0, "Sovuq": 0, "Issiq": 0, "1-sotuv": 0,
                      "Doimiy": 0, "Uxlab qoldi": 0}
            for c in clients_db.values():
                s = _report_status(c, now_ts)
                if s and s in counts:
                    counts[s] += 1
            sovuq_phones = {
                fu["phone"] for fu in follow_ups
                if fu.get("type") == "sovuq_lid" and fu.get("sent")
            }
            counts["Sovuq"] += len(sovuq_phones)
            total = sum(counts.values())
            report = (
                f"Haftalik hisobot ({now.strftime('%d.%m.%Y')}):\n\n"
                f"Jami: {total}\n"
                f"Yangi: {counts['Yangi']}\n"
                f"Sovuq: {counts['Sovuq']}\n"
                f"Issiq: {counts['Issiq']}\n"
                f"1-sotuv: {counts['1-sotuv']}\n"
                f"Doimiy: {counts['Doimiy']}\n"
                f"Uxlab qoldi: {counts['Uxlab qoldi']}"
            )
            try:
                await client.send_message(BOSS_CHAT_ID, report)
            except Exception as e:
                logger.error(f"Haftalik hisobot xato: {e}")

            # Haftalik sotuv hisoboti (Supabase)
            if _DB_OK:
                rows = await asyncio.to_thread(_db.get_haftalik_sotuv)
                if rows:
                    lines = [f"Haftalik sotuv ({now.strftime('%d.%m.%Y')}, so'nggi 7 kun):\n"]
                    jami = 0
                    for r in rows:
                        kg = r.get("jami_kg") or 0
                        jami += kg
                        lines.append(
                            f"{r['marka']}: {int(kg):,} kg ({r['buyurtmalar_soni']} ta)".replace(",", " ")
                        )
                    lines.append(f"\nJami: {int(jami):,} kg".replace(",", " "))
                    etirozlar = await asyncio.to_thread(_db.get_etirozlar_taqsimoti)
                    hafta_etirozlar = [r for r in etirozlar if r.get("haftalik", 0)]
                    if hafta_etirozlar:
                        lines.append("\nE'tirozlar (so'nggi 7 kun):")
                        for r in hafta_etirozlar:
                            lines.append(f"  {r['etiroz_turi']}: {r['haftalik']} ta")
                    try:
                        await client.send_message(BOSS_CHAT_ID, "\n".join(lines))
                    except Exception as e:
                        logger.error(f"Haftalik sotuv hisoboti xato: {e}")

        # ── Oylik: har oy 1-kuni ──
        if now.day == 1 and _DB_OK:
            rows = await asyncio.to_thread(_db.get_oylik_sotuv)
            if rows:
                lines = [f"Oylik sotuv hisoboti ({now.strftime('%m.%Y')}):\n"]
                jami = 0
                for r in rows:
                    kg = r.get("jami_kg") or 0
                    jami += kg
                    lines.append(
                        f"{r['marka']}: {int(kg):,} kg ({r['buyurtmalar_soni']} ta)".replace(",", " ")
                    )
                lines.append(f"\nJami: {int(jami):,} kg".replace(",", " "))
                etirozlar = await asyncio.to_thread(_db.get_etirozlar_taqsimoti)
                oy_etirozlar = [r for r in etirozlar if r.get("oylik", 0)]
                if oy_etirozlar:
                    lines.append("\nE'tirozlar (so'nggi 30 kun):")
                    for r in oy_etirozlar:
                        lines.append(f"  {r['etiroz_turi']}: {r['oylik']} ta")
                try:
                    await client.send_message(BOSS_CHAT_ID, "\n".join(lines))
                except Exception as e:
                    logger.error(f"Oylik sotuv hisoboti xato: {e}")


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
    global clients_db, follow_ups, current_prices, kanonik_markalar_ro, soz_sinonimlar_ro
    follow_ups = _load_follow_ups()
    logger.info(f"Follow-ups yuklandi: {len(follow_ups)} ta")
    if _DB_OK:
        current_prices = _db.get_all_narxlar()
        kanonik_markalar_ro = _db.get_kanonik_markalar()
        soz_sinonimlar_ro = _db.get_soz_sinonimlar()
        logger.info(f"Narxlar Supabase'dan yuklandi: {len(current_prices)} ta")
        logger.info(f"Kanonik markalar yuklandi: {len(kanonik_markalar_ro)} ta")
    else:
        current_prices = _load_prices()
    logger.info(f"Narxlar yuklandi: {len(current_prices)} ta marka")
    if _SHEETS_ID and _GC_RAW:
        token = _get_sheets_token()
        if token:
            _sheets_ensure_mijozlar(token)
            _sheets_ensure_lidlar(token)
        clients_db = sheets_load_clients()
    else:
        logger.warning("GOOGLE_SHEET_ID yoki GOOGLE_CREDENTIALS yo'q — JSON fayldan yuklanadi.")
        clients_db = _load_clients_json()
    logger.info(f"clients_db yuklandi: {len(clients_db)} ta mijoz")

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
        follow_up_checker(),
        reporter(),
        client.run_until_disconnected(),
    )


asyncio.run(main())
