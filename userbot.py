import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta

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

last_price_message: str | None = None
conversations: dict = {}        # {chat_id: [{"role": ..., "content": ...}]}
clients_db: dict = {}           # {chat_id: {"name": ..., "telegram": ...}}
pending_price_requests: dict = {}        # {customer_id: {"marka": str, "miqdor": str}}
pending_bank_requests: dict = {}         # {customer_id: {"marka": str, "miqdor": str}}
pending_price_negotiations: dict = {}    # {customer_id: {"marka": str, "taklif": str, "asl": str}}

FOLLOW_UPS_FILE = "follow_ups.json"
follow_ups: list = []
_lidlar_init_done: bool = False


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

E'TIROZLAR:
"Qimmat" desa: "Qayerda ko'rdingiz?" de
"O'ylab ko'raman" desa: "Narxdan tashqari boshqa savol bormi?"
"Boshqa joy arzon" desa: "Qancha farq bor?" de

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
    return "Rus" if cyrillic / max(len(text), 1) > 0.3 else "O'zbek"


def _sheets_ensure_lidlar(token: str) -> None:
    hdrs = {"Authorization": f"Bearer {token}"}
    meta = _http.get(_SHEETS_BASE, headers=hdrs, timeout=10)
    if meta.ok:
        titles = [s["properties"]["title"] for s in meta.json().get("sheets", [])]
        if _LIDLAR_SHEET not in titles:
            _http.post(f"{_SHEETS_BASE}:batchUpdate", headers=hdrs, timeout=10,
                       json={"requests": [{"addSheet": {"properties": {"title": _LIDLAR_SHEET}}}]})
    rng = urllib.parse.quote(f"{_LIDLAR_SHEET}!A1:M1", safe="")
    _http.put(f"{_SHEETS_BASE}/values/{rng}", headers=hdrs, timeout=10,
              params={"valueInputOption": "RAW"},
              json={"values": [_LIDLAR_HEADER]})


def _lidlar_append(row: list) -> None:
    global _lidlar_init_done
    token = _get_sheets_token()
    if not token:
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
    if not r.ok:
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


def build_lead_card(chat_id: int, phone: str, response_text: str) -> str:
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

        # /yordam — barcha buyruqlar ro'yxati
        if text.strip().lower() == "/yordam":
            pending_names = ", ".join(
                clients_db[cid].get("name", str(cid))
                for cid in list(pending_price_requests) + list(pending_bank_requests)
            ) or "yo'q"
            await event.respond(
                "Buyruqlar:\n"
                "/yordam — shu ro'yxat\n"
                "/yoz [ism] [xabar] — mijozga xabar yuborish\n\n"
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

        global last_price_message
        last_price_message = text
        notified_str = ", ".join(notified) if notified else "yo'q"
        logger.info(f"BOSS narx xabari saqlandi. Xabardor qilingan: {notified_str}")
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
            "til": detect_language(text),
        }
        asyncio.create_task(asyncio.to_thread(sheets_save_client, sender_id, clients_db[sender_id]))
        asyncio.create_task(asyncio.to_thread(
            sheets_lidlar_customer, sender_id, clients_db[sender_id], text
        ))
        logger.info(f"Yangi mijoz: {clients_db[sender_id]['name']} {username}")

    clients_db[sender_id]["last_msg_ts"] = datetime.now(TASHKENT).timestamp()

    response = await get_ai_response(sender_id, text)
    customer_text, markers = parse_response(response)

    if "ism" in markers:
        clients_db[sender_id]["name"] = markers["ism"]
        asyncio.create_task(asyncio.to_thread(sheets_save_client, sender_id, clients_db[sender_id]))

    if "issiq_lid" in markers:
        phone = extract_phone(text) if has_valid_phone(text) else "?"
        await event.respond("Rahmat, tez orada bog'lanamiz.")
        card = build_lead_card(sender_id, phone, response)
        await client.send_message(BOSS_CHAT_ID, card)
        logger.info(f"Issiq lid BOSS ga yuborildi: {clients_db[sender_id].get('name', sender_id)} tel={phone}")
        # Marka ni ajratib olish
        lid_details = {}
        for line in response.strip().split("\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                lid_details[k.strip()] = v.strip()
        lid_marka = lid_details.get("Marka", "mahsulot")
        clients_db[sender_id]["had_issiq_lid"] = True
        clients_db[sender_id]["last_marka"] = lid_marka
        schedule_follow_up("issiq_lid", sender_id, lid_marka, 4)
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
            msg = last_price_message + "\n\n📞 +998907080000\n✈️ @nargiza_petroplast"
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

        await asyncio.sleep(3600)


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
    global clients_db, follow_ups
    follow_ups = _load_follow_ups()
    logger.info(f"Follow-ups yuklandi: {len(follow_ups)} ta")
    if _SHEETS_ID and _GC_RAW:
        token = _get_sheets_token()
        if token:
            _sheets_ensure_mijozlar(token)
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
        client.run_until_disconnected(),
    )


asyncio.run(main())
