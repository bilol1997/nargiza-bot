import asyncio
import os
import re
import json
import time
import base64
import logging
import urllib.parse
from datetime import datetime, timedelta
import pytz
import anthropic
import openai
import requests as _http
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as _rsa_padding
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

try:
    from dotenv import load_dotenv
    load_dotenv()
    import db as _db
    _DB_OK = True
except Exception as _db_err:
    logger = logging.getLogger(__name__)
    logger.warning(f"Supabase db moduli yuklanmadi: {_db_err}")
    _DB_OK = False
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

TASHKENT = pytz.timezone("Asia/Tashkent")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOSS_CHAT_ID = int(os.environ.get("BOSS_CHAT_ID", "0"))
claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

# ── Google Sheets ─────────────────────────────────────────────────────────────
_SHEET_ID = "1VcRgmY8b6CLk-E-DjVS4SBoMfU9qB9nvoc-S-ia_9yk"
_SHEETS_BASE = f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}"
_token_cache: dict = {"token": None, "exp": 0}
_MIJOZLAR_SHEET = "Mijozlar"
_MIJOZLAR_HEADER = ["chat_id", "name", "telegram", "sana", "til", "status", "lid"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _get_sheets_token() -> str | None:
    now = int(time.time())
    if _token_cache["token"] and now < _token_cache["exp"]:
        return _token_cache["token"]
    raw = os.environ.get("GOOGLE_CREDENTIALS", "")
    if not raw or not _CRYPTO_OK:
        return None
    try:
        info = json.loads(raw)
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    except Exception as e:
        logger.error(f"GOOGLE_CREDENTIALS parse xatosi: {e}")
        return None
    try:
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps({
            "iss": info["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now, "exp": now + 3600,
        }).encode())
        msg = f"{header}.{payload}".encode()
        pk = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        sig = pk.sign(msg, _rsa_padding.PKCS1v15(), hashes.SHA256())
        jwt = f"{header}.{payload}.{_b64url(sig)}"
    except Exception as e:
        logger.error(f"JWT xatosi: {e}")
        return None
    try:
        resp = _http.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt},
            timeout=10,
        )
        token = resp.json().get("access_token")
        _token_cache["token"] = token
        _token_cache["exp"] = now + 3000
        return token
    except Exception as e:
        logger.error(f"Token xatosi: {e}")
        return None


_HEADER = ["Sana", "Ism", "Telefon", "Marka", "Miqdor", "To'lov", "Narx", "Holat",
           "Telegram", "Status", "Til", "Sikl", "Izoh"]


def _sheets_ensure_headers(token: str):
    hdrs = {"Authorization": f"Bearer {token}"}
    # Varaq yo'q bo'lsa yaratish
    meta = _http.get(_SHEETS_BASE, headers=hdrs, timeout=10)
    if meta.ok:
        titles = [s["properties"]["title"] for s in meta.json().get("sheets", [])]
        if "Lidlar" not in titles:
            _http.post(f"{_SHEETS_BASE}:batchUpdate", headers=hdrs, timeout=10,
                       json={"requests": [{"addSheet": {"properties": {"title": "Lidlar"}}}]})
    rng = urllib.parse.quote("Lidlar!A1:M1", safe="")
    _http.put(f"{_SHEETS_BASE}/values/{rng}", headers=hdrs, timeout=10,
              params={"valueInputOption": "RAW"},
              json={"values": [_HEADER]})


_sheets_init_done = False
_mijozlar_init_done = False
HOLATLAR = ["Yangi", "Qayta aloqa", "Sotildi", "Rad etdi"]


def sheets_get_leads(limit=10):
    """Return list of (sheet_row_number, row_list) for last N leads."""
    token = _get_sheets_token()
    if not token:
        return []
    try:
        rng = urllib.parse.quote("Lidlar!A:M", safe="")
        r = _http.get(f"{_SHEETS_BASE}/values/{rng}",
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if not r.ok:
            return []
        all_rows = r.json().get("values", [])
        data_rows = all_rows[1:]  # header ni o'tkazib yubor
        start = max(0, len(data_rows) - limit)
        result = []
        for i, row in enumerate(data_rows[start:], start=start):
            sheet_row = i + 2  # 1-based + header qatori
            result.append((sheet_row, row))
        return result
    except Exception as e:
        logger.error(f"sheets_get_leads: {e}")
        return []


def sheets_update_status(sheet_row, new_status):
    """Update column H (Holat) for given sheet row number."""
    token = _get_sheets_token()
    if not token:
        return False
    try:
        cell = urllib.parse.quote(f"Lidlar!H{sheet_row}", safe="")
        r = _http.put(
            f"{_SHEETS_BASE}/values/{cell}",
            headers={"Authorization": f"Bearer {token}"},
            params={"valueInputOption": "RAW"},
            json={"values": [[new_status]]},
            timeout=10,
        )
        return r.ok
    except Exception as e:
        logger.error(f"sheets_update_status: {e}")
        return False


def _sheets_append(token: str, row: list) -> None:
    global _sheets_init_done
    if not _sheets_init_done:
        _sheets_ensure_headers(token)
        _sheets_init_done = True
    rng = urllib.parse.quote("Lidlar!A:M", safe="")
    r = _http.post(
        f"{_SHEETS_BASE}/values/{rng}:append",
        headers={"Authorization": f"Bearer {token}"},
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"values": [row]},
        timeout=10,
    )
    if not r.ok:
        logger.error(f"Sheets append {r.status_code}: {r.text[:150]}")


def detect_language(text: str) -> str:
    if not text:
        return ""
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    return "Rus" if cyrillic / max(len(text), 1) > 0.3 else "O'zbek"


def sheets_add_customer(chat_id: int, name: str, telegram: str, first_text: str = "") -> None:
    token = _get_sheets_token()
    if not token:
        return
    try:
        til = detect_language(first_text)
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            name, "", "", "", "", "", "",          # Telefon..Holat bo'sh
            telegram, "Yangi", til, "Birinchi aloqa", ""
        ]
        _sheets_append(token, row)
        logger.info(f"Sheets yangi mijoz: {name} {telegram}")
    except Exception as e:
        logger.error(f"sheets_add_customer: {e}")


def sheets_add_lead(name, phone, marka, miqdor, tolov, narx,
                    telegram="", til="", holat="Yangi lid"):
    token = _get_sheets_token()
    if not token:
        return
    try:
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            name, phone, marka, miqdor, tolov, narx, holat,
            telegram, "Issiq lid", til, "Buyurtma", ""
        ]
        _sheets_append(token, row)
        logger.info(f"Sheets lead: {name}, {marka}")
    except Exception as e:
        logger.error(f"sheets_add_lead: {e}")


def _sheets_ensure_mijozlar(token: str) -> None:
    hdrs = {"Authorization": f"Bearer {token}"}
    meta = _http.get(_SHEETS_BASE, headers=hdrs, timeout=10)
    if meta.ok:
        titles = [s["properties"]["title"] for s in meta.json().get("sheets", [])]
        if _MIJOZLAR_SHEET not in titles:
            _http.post(f"{_SHEETS_BASE}:batchUpdate", headers=hdrs, timeout=10,
                       json={"requests": [{"addSheet": {"properties": {"title": _MIJOZLAR_SHEET}}}]})
    rng = urllib.parse.quote(f"{_MIJOZLAR_SHEET}!A1:G1", safe="")
    _http.put(f"{_SHEETS_BASE}/values/{rng}", headers=hdrs, timeout=10,
              params={"valueInputOption": "RAW"},
              json={"values": [_MIJOZLAR_HEADER]})


def sheets_save_mijoz(chat_id: int, data: dict) -> None:
    global _mijozlar_init_done
    token = _get_sheets_token()
    if not token:
        return
    try:
        if not _mijozlar_init_done:
            _sheets_ensure_mijozlar(token)
            _mijozlar_init_done = True
        row = [
            str(chat_id),
            data.get("name", ""),
            data.get("telegram", ""),
            data.get("date", datetime.now(TASHKENT).strftime("%Y-%m-%d %H:%M")),
            data.get("til", ""),
            data.get("category", "Yangi"),
            "True" if data.get("had_issiq_lid") else "False",
        ]
        rng = urllib.parse.quote(f"{_MIJOZLAR_SHEET}!A:G", safe="")
        r = _http.post(
            f"{_SHEETS_BASE}/values/{rng}:append",
            headers={"Authorization": f"Bearer {token}"},
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": [row]},
            timeout=10,
        )
        if not r.ok:
            logger.error(f"Mijozlar append {r.status_code}: {r.text[:150]}")
        else:
            logger.info(f"Mijozlar saqlandi: {data.get('name')} {data.get('telegram')}")
    except Exception as e:
        logger.error(f"sheets_save_mijoz: {e}")


def sheets_load_mijozlar() -> dict:
    token = _get_sheets_token()
    if not token:
        return {}
    try:
        rng = urllib.parse.quote(f"{_MIJOZLAR_SHEET}!A:G", safe="")
        r = _http.get(
            f"{_SHEETS_BASE}/values/{rng}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if not r.ok:
            return {}
        all_rows = r.json().get("values", [])
        if len(all_rows) < 2:
            return {}
        result = {}
        for row in all_rows[1:]:
            if not row:
                continue
            try:
                cid = int(row[0])
            except (ValueError, IndexError):
                continue
            result[cid] = {
                "name": row[1] if len(row) > 1 else "",
                "telegram": row[2] if len(row) > 2 else "",
                "date": row[3] if len(row) > 3 else "",
                "til": row[4] if len(row) > 4 else "",
                "category": row[5] if len(row) > 5 else "Yangi",
                "had_issiq_lid": (row[6].lower() == "true") if len(row) > 6 else False,
            }
        logger.info(f"Mijozlar yuklandi: {len(result)} ta")
        return result
    except Exception as e:
        logger.error(f"sheets_load_mijozlar: {e}")
        return {}


conversations = {}
clients_db = {}
current_prices = {}
pending_price_negotiations = {}
pending_price_requests = {}

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
    """Claude Haiku orqali BOSS xabaridan niyat aniqlanadi."""
    def _sync_call():
        return claude_client.messages.create(
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

FOLLOW_UPS_FILE = "follow_ups_bot.json"
follow_ups: list = []
context_store: dict = {}  # kunlik hisobot va boshqa bir martalik flaglar uchun


_MALE_NAMES = {
    "jasur", "jamshid", "bobur", "sardor", "sanjar", "sherzod", "shahzod",
    "ulugbek", "laziz", "otabek", "alisher", "nodir", "mansur", "rustam",
    "doston", "timur", "bahodir", "murod", "oybek", "diyorbek", "bekzod",
    "farrux", "islom", "eldor", "umid", "akbar", "kamol", "zafar", "davron",
    "firdavs", "jahongir", "abror", "mirzo", "dilshod", "hamza", "anvar",
    "nuriddin", "ilhom", "elmurod", "qodir", "tohir", "elbek", "komil",
    "muzaffar", "ibrohim", "ismoil", "yusuf", "abdulloh", "muhammad", "ahmad",
    "ali", "umar", "rahim", "nurbek", "ravshan", "shuhrat", "suxrob", "vohid",
    "xurshid", "hasan", "husayn", "javlon", "bahrom", "behruz", "bilol",
    "botirbek", "bunyod", "bunyodbek", "doniyor", "erkin", "farhod", "furqat",
    "hayot", "hikmat", "humoyun", "iskandar", "javohir", "lochin", "mustafo",
    "nozim", "nurillo", "obid", "ortiq", "ozod", "rauf", "salim", "sarvar",
    "sirojiddin", "sobir", "sodiq", "sulton", "toxir", "ulmas", "uygun",
    "xasan", "yahyo", "yoqub", "zafarjon", "zohid", "abdulaziz", "asliddin",
    "asror", "azamat", "azizbek", "baxtiyor", "doniybek", "eldorbek",
    "feruzbek", "husan", "husanboy", "islombek", "kenja", "muxammad",
    "nurullo", "ortiqboy", "otajon", "sarvarjon", "sunnat", "tohirjon",
    "xurshidbek", "zokirjon", "bekhzod", "bexruz",
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
    "komila", "malohat", "mashxura", "mavzuna", "mohichehra", "mohinur",
    "mukaddas", "nafosat", "nodira", "noila", "parizod", "ruxshona", "sevara",
    "shoira", "shohida", "tursunoy", "xadicha", "zamira", "zarnigor",
    "ziyoda", "nafosatxon", "mahliyo", "mohlaroyim",
}


def name_title(name: str) -> str:
    if not name:
        return name
    first = name.strip().split()[0].lower()
    if first in _MALE_NAMES:
        return f"{name} aka"
    if first in _FEMALE_NAMES:
        return f"{name} opa"
    return name


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
    now_ts = time.time()
    follow_ups.append({
        "type": ftype,
        "customer_id": customer_id,
        "marka": marka,
        "created_ts": now_ts,
        "send_ts": now_ts + days * 86400,
        "sent": False,
    })
    _save_follow_ups()
    logger.info(f"Follow-up rejalashtirildi: {ftype} | cid={customer_id} | {days} kun")


async def follow_up_checker(bot) -> None:
    await asyncio.sleep(60)
    while True:
        now = datetime.now(TASHKENT)
        now_ts = time.time()
        if not (9 <= now.hour < 18):
            await asyncio.sleep(3600)
            continue

        changed = False

        for fu in follow_ups:
            if fu["sent"] or now_ts < fu["send_ts"]:
                continue
            customer_id = fu["customer_id"]
            c = clients_db.get(customer_id, {})
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
                await bot.send_message(chat_id=customer_id, text=msg)
                fu["sent"] = True
                changed = True
                logger.info(f"Follow-up yuborildi: {fu['type']} | {customer_id} | {marka}")
            except Exception as e:
                logger.error(f"Follow-up xato ({customer_id}): {e}")

        for customer_id, c in list(clients_db.items()):
            if not c.get("had_issiq_lid"):
                continue
            last_ts = c.get("last_msg_ts", 0)
            if not last_ts or (now_ts - last_ts) < 10 * 86400:
                continue
            if (now_ts - c.get("last_f3_ts", 0)) < 20 * 86400:
                continue
            marka = c.get("last_marka", "mahsulot")
            titled = name_title(c.get("name", "")) or "Salom"
            msg = f"{titled}, salom! {marka} qoldiqlari tugab qolmadimi? Yangi partiya bor."
            try:
                await bot.send_message(chat_id=customer_id, text=msg)
                clients_db[customer_id]["last_f3_ts"] = now_ts
                changed = True
                logger.info(f"Doimiy mijoz follow-up: {customer_id} | {marka}")
            except Exception as e:
                logger.error(f"Doimiy mijoz follow-up xato ({customer_id}): {e}")

        if changed:
            _save_follow_ups()

        # Kunlik hisobot: har kuni 20:00 da kutilmoqda buyurtmalar
        if _DB_OK and now.hour == 20:
            hisobot_key = f"hisobot_{now.date()}"
            if not context_store.get(hisobot_key):
                context_store[hisobot_key] = True
                rows = await asyncio.to_thread(_db.get_kutilmoqda_buyurtmalar)
                if rows:
                    lines = ["Kutilmoqda buyurtmalar:\n"]
                    for r in rows:
                        mijoz   = r.get("mijozlar") or {}
                        ism     = mijoz.get("ism") or str(r["mijoz_id"])
                        tel     = mijoz.get("telefon") or "—"
                        miqdor  = f"{r['miqdor']} {r['birlik']}" if r.get("miqdor") else "?"
                        lines.append(
                            f"#{r['id']} | {ism} ({tel}) | {r['marka']} | {miqdor}"
                        )
                    lines.append("\nJavob formati: '42 sotildi 500' yoki '42 sotilmadi'")
                    try:
                        await bot.send_message(
                            chat_id=BOSS_CHAT_ID,
                            text="\n".join(lines),
                        )
                    except Exception as e:
                        logger.error(f"Kunlik hisobot xato: {e}")
                else:
                    logger.info("20:00 hisobot: kutilmoqda buyurtma yo'q.")

        await asyncio.sleep(3600)


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


def match_marka(stored, parsed):
    """Flexible brand match: exact → case-insensitive → prefix."""
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


def calc_total(narx, miqdor_str):
    """Calculate total price. Returns (kg_amount, total) or (None, None)."""
    try:
        num = float(re.sub(r'[^0-9.]', '', miqdor_str))
        if not num:
            return None, None
        kg = num * 1000 if 'tonn' in miqdor_str.lower() else num
        return kg, narx * kg
    except (ValueError, TypeError):
        return None, None


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
                    m2 = re.search(r'[\d][\d\s,]*', parts[1].strip())
                    if m2 and brand:
                        price_str = re.sub(r'[^\d]', '', m2.group())
                        if price_str:
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
        return True
    except Exception as e:
        logger.error(f"Customer notify error (chat_id={chat_id}): {e}")
        return False


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
        card = build_lead_card(chat_id, text, response)
        await notify_boss(context, card)
        # Google Sheets ga yoz
        c = clients_db.get(chat_id, {})
        details = {}
        for line in response.strip().split('\n')[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                details[k.strip()] = v.strip()
        lid_marka = details.get('Marka', 'mahsulot')
        sheets_add_lead(
            name=c.get('name', '?'),
            phone=extract_phone(text),
            marka=lid_marka,
            miqdor=details.get('Miqdor', '?'),
            tolov=details.get("To'lov", '?'),
            narx=details.get('Narx', '?'),
            telegram=c.get('telegram', ''),
            til=c.get('til', ''),
        )
        if chat_id in clients_db:
            clients_db[chat_id]['category'] = 'Issiq'
            clients_db[chat_id]['had_issiq_lid'] = True
            clients_db[chat_id]['last_marka'] = lid_marka
        asyncio.create_task(asyncio.to_thread(sheets_save_mijoz, chat_id, clients_db.get(chat_id, {})))
        schedule_follow_up("issiq_lid", chat_id, lid_marka, 4)
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
        if chat_id in clients_db:
            clients_db[chat_id]['last_marka'] = marka
        c = clients_db.get(chat_id, {})
        miqdor_text = f", {miqdor}" if miqdor and miqdor != '?' else ""
        await notify_boss(
            context,
            f"NARX SO'ROVI:\n"
            f"Mijoz: {c.get('name', '?')} {c.get('telegram', '')}\n"
            f"Marka: {marka}{miqdor_text}\n"
            f"/narx bilan narx kiriting — mijozga avtomatik yuboriladi.\n"
            f"Yoki: 'mijozga ayt [narx]' deb yozing."
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_boss(chat_id):
        await update.message.reply_text(
            "Salom Boss!\n"
            "/narx - narx kiritish\n"
            "/holat_ozgartir - lid holatini o'zgartir\n"
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
        logger.info(f"handle_text BOSS: xabar keldi -> {text!r}")
        low = text.lower().strip()

        # "<id> sotildi [miqdor]" yoki "<id> sotilmadi"
        if _DB_OK:
            _sotildi_re  = re.compile(
                r'^(\d+)\s+sotildi(?:\s+([\d.,]+)\s*(kg|tonna)?)?$', re.IGNORECASE
            )
            _sotilmadi_re = re.compile(r'^(\d+)\s+sotilmadi$', re.IGNORECASE)
            m_sotildi   = _sotildi_re.match(low)
            m_sotilmadi = _sotilmadi_re.match(low)

            if m_sotildi:
                buyurtma_id = int(m_sotildi.group(1))
                miqdor_raw  = m_sotildi.group(2)
                birlik      = (m_sotildi.group(3) or "kg").lower()
                miqdor      = float(miqdor_raw.replace(",", ".")) if miqdor_raw else 0.0
                if birlik == "tonna":
                    miqdor *= 1000
                res = await asyncio.to_thread(_db.tasdiqla_buyurtma, buyurtma_id, miqdor)
                if res["ok"]:
                    await update.message.reply_text(
                        f"Tasdiqlandi: #{buyurtma_id} {res['marka']} — sotildi."
                    )
                elif res["sabab"] == "topilmadi":
                    await update.message.reply_text(
                        f"#{buyurtma_id} — bunday buyurtma topilmadi."
                    )
                else:
                    await update.message.reply_text(
                        f"#{buyurtma_id} allaqachon '{res.get('status', '?')}' holatida."
                    )
                return

            if m_sotilmadi:
                buyurtma_id = int(m_sotilmadi.group(1))
                res = await asyncio.to_thread(_db.bekor_qil_buyurtma, buyurtma_id)
                if res["ok"]:
                    await update.message.reply_text(
                        f"Bekor qilindi: #{buyurtma_id} {res['marka']}."
                    )
                elif res["sabab"] == "topilmadi":
                    await update.message.reply_text(
                        f"#{buyurtma_id} — bunday buyurtma topilmadi."
                    )
                else:
                    await update.message.reply_text(
                        f"#{buyurtma_id} allaqachon '{res.get('status', '?')}' holatida."
                    )
                return

        if context.user_data.get('awaiting_holat'):
            context.user_data['awaiting_holat'] = False
            leads = context.user_data.pop('holat_leads', [])
            parts = text.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                idx = int(parts[0]) - 1
                new_status_raw = parts[1].strip()
                matched = next((h for h in HOLATLAR if h.lower() == new_status_raw.lower()), None)
                if not matched:
                    await update.message.reply_text(
                        f"Noto'g'ri holat. Tanlov: {' / '.join(HOLATLAR)}"
                    )
                    return
                new_status = matched
                if 0 <= idx < len(leads):
                    sheet_row, row = leads[idx]
                    ok = sheets_update_status(sheet_row, new_status)
                    name = row[1] if len(row) > 1 else '?'
                    if ok:
                        await update.message.reply_text(
                            f"✅ {name} — holat '{new_status}' ga o'zgartirildi."
                        )
                    else:
                        await update.message.reply_text("Sheets xatosi. Keyinroq urinib ko'ring.")
                else:
                    await update.message.reply_text("Noto'g'ri raqam.")
            else:
                await update.message.reply_text(
                    "Format: [raqam] [holat]\nMasalan: 2 Sotildi"
                )
            return

        if context.user_data.get('awaiting_narx'):
            context.user_data['awaiting_narx'] = False
            parsed = parse_price_list(text)
            if parsed:
                current_prices.clear()
                current_prices.update(parsed)
                prices_text = "\n".join([f"{k}: {v:,}" for k, v in parsed.items()])
                await update.message.reply_text(f"Narxlar saqlandi!\n{prices_text}")
                logger.info(f"pending_price_requests: {pending_price_requests}")
                notified = []
                failed = []
                for cust_id, req in list(pending_price_requests.items()):
                    stored_marka = req.get('marka', '')
                    matched_key, narx = match_marka(stored_marka, parsed)
                    if narx is None:
                        continue
                    miqdor_str = req.get('miqdor', '?')
                    c = clients_db.get(cust_id, {})
                    name = c.get('name', '')
                    name_prefix = f"{name}, yaxshi xabar! " if name else "Yaxshi xabar! "
                    msg = f"{name_prefix}{matched_key} narxi: {narx:,} so'm/kg."
                    if miqdor_str and miqdor_str != '?':
                        kg, total = calc_total(narx, miqdor_str)
                        if total is not None:
                            msg += f"\n{miqdor_str} uchun jami: {int(total):,} so'm."
                    msg += "\nBuyurtmani tasdiqlaysizmi?"
                    ok = await send_customer(context, cust_id, msg)
                    if ok:
                        pending_price_requests.pop(cust_id, None)
                        notified.append(f"{name or cust_id} ({matched_key})")
                        schedule_follow_up("narx_javob_yoq", cust_id, matched_key, 3)
                    else:
                        failed.append(f"{name or cust_id} ({matched_key}) — xabar yetmadi!")
                status_lines = []
                if notified:
                    status_lines.append(f"Yuborildi: {', '.join(notified)}")
                if failed:
                    status_lines.append(f"XATO: {', '.join(failed)}")
                if not notified and not failed and pending_price_requests:
                    still = [
                        f"{clients_db.get(cid, {}).get('name', cid)}: {r.get('marka', '?')}"
                        for cid, r in pending_price_requests.items()
                    ]
                    status_lines.append("Marka mos kelmadi:\n" + "\n".join(still))
                if status_lines:
                    await update.message.reply_text("\n".join(status_lines))
            else:
                await update.message.reply_text("Format noto'g'ri. Qaytadan /narx yuboring.")
            return

        # "Mijozga ayt/yoz [narx]" — kutayotgan mijozga narx yuborish
        if any(phrase in low for phrase in ['mijozga ayt', 'mijozga yoz', 'mijozga yubor']):
            if not pending_price_requests:
                await update.message.reply_text("Narx kutayotgan mijoz yo'q.")
                return
            cust_id, req = list(pending_price_requests.items())[-1]
            c = clients_db.get(cust_id, {})
            marka = req.get('marka', '?')
            miqdor_str = req.get('miqdor', '?')
            text_digits = re.sub(r'[^\d\s]', ' ', text)
            price_match = re.search(r'\d[\d ]*\d|\d{4,}', text_digits)
            if price_match:
                narx = int(re.sub(r'\s', '', price_match.group()))
            else:
                narx = current_prices.get(marka)
            if not narx:
                await update.message.reply_text(
                    f"{marka} narxi topilmadi. Masalan: 'mijozga ayt 20400'"
                )
                return
            name = c.get('name', '')
            name_prefix = f"{name}, yaxshi xabar! " if name else "Yaxshi xabar! "
            msg = f"{name_prefix}{marka} narxi: {narx:,} so'm/kg."
            if miqdor_str and miqdor_str != '?':
                kg, total = calc_total(narx, miqdor_str)
                if total is not None:
                    msg += f"\n{miqdor_str} uchun jami: {int(total):,} so'm."
            msg += "\nBuyurtmani tasdiqlaysizmi?"
            ok = await send_customer(context, cust_id, msg)
            pending_price_requests.pop(cust_id, None)
            if ok:
                schedule_follow_up("narx_javob_yoq", cust_id, marka, 3)
            await update.message.reply_text(f"Yuborildi: {msg}")
            return

        # Boss narx kelishuvini tasdiqlaydi: "ha 20400", "ha [20,400]", "ruxsat berdi" va h.k.
        if pending_price_negotiations and any(w in low for w in [
            'ha ', 'ruxsat', 'tasdiqlandi', 'berish mumkin', 'beramiz', 'roziman', 'ok'
        ]):
            text_digits = re.sub(r'[^\d\s]', ' ', text)  # [20,400] → " 20 400 "
            price_match = re.search(r'\d[\d ]*\d|\d{4,}', text_digits)
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

        # ── Pending intent tasdiq (Ha/Yo'q) ──────────────────────────────────
        pending = context.user_data.get("pending_intent")
        if pending:
            age = time.time() - pending.get("ts", 0)
            if age > 300:
                context.user_data.pop("pending_intent", None)
                await update.message.reply_text("Vaqt o'tdi, amal bekor qilindi.")
                return
            if low in ("ha", "ha.", "yes", "ok", "+"):
                context.user_data.pop("pending_intent", None)
                niyat = pending["niyat"]
                if niyat == "narx_yangilash":
                    marka = pending["parametrlar"]["marka"]
                    narx  = pending["parametrlar"]["narx"]
                    current_prices[marka] = narx
                    logger.info(f"Narx yangilandi: {marka} = {narx}")
                    await update.message.reply_text(
                        f"{marka} narxi {narx:,} ga o\\'rnatildi.".replace(",", " ")
                    )
                return
            if low in ("yo'q", "yoq", "no", "bekor", "-"):
                context.user_data.pop("pending_intent", None)
                await update.message.reply_text("Bekor qilindi.")
                return
            context.user_data.pop("pending_intent", None)

        # ── Intent router: narx yangilash ─────────────────────────────────────
        logger.info(f"handle_text BOSS: intent_router ga yetdi -> {text!r}")
        result = await intent_router(text)
        if result.get("niyat") == "narx_yangilash":
            p = result.get("parametrlar", {})
            marka = str(p.get("marka", "")).strip()
            try:
                narx = int(p.get("narx", 0))
            except (TypeError, ValueError):
                narx = 0
            if marka and narx > 0:
                context.user_data["pending_intent"] = {
                    "niyat": "narx_yangilash",
                    "parametrlar": {"marka": marka, "narx": narx},
                    "ts": time.time(),
                }
                await update.message.reply_text(
                    f"{marka} narxini {narx:,} ga o\\'zgartiraymi? Ha/Yo\\'q".replace(",", " ")
                )
                return

        return

    is_new = chat_id not in clients_db
    if is_new:
        tg = f"@{update.effective_user.username}" if update.effective_user.username else ''
        clients_db[chat_id] = {
            'name': update.effective_user.first_name or '',
            'telegram': tg,
            'category': 'Yangi',
            'date': datetime.now(TASHKENT).strftime("%Y-%m-%d %H:%M"),
            'til': detect_language(text),
        }
        sheets_add_customer(chat_id, clients_db[chat_id]['name'], tg, text)
        asyncio.create_task(asyncio.to_thread(sheets_save_mijoz, chat_id, clients_db[chat_id]))
    clients_db[chat_id]['last_msg_ts'] = time.time()

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
        context.user_data['last_photo'] = update.message.photo[-1].file_id
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


async def cmd_holat_ozgartir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    leads = sheets_get_leads(10)
    if not leads:
        await update.message.reply_text(
            "Sheets da ma'lumot topilmadi yoki ulanish yo'q."
        )
        return
    context.user_data['holat_leads'] = leads
    context.user_data['awaiting_holat'] = True
    lines = ["So'nggi lidlar:\n"]
    for i, (_, row) in enumerate(leads, 1):
        name  = row[1] if len(row) > 1 else '?'
        marka = row[3] if len(row) > 3 else '?'
        holat = row[7] if len(row) > 7 else '?'
        lines.append(f"{i}. {name} — {marka} [{holat}]")
    lines.append(f"\nHolatni o'zgartirish: [raqam] [holat]")
    lines.append(f"Masalan: 2 Sotildi")
    lines.append(f"\nHolatlar: {' / '.join(HOLATLAR)}")
    await update.message.reply_text("\n".join(lines))


async def cmd_narx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_boss(update.effective_chat.id):
        return
    context.user_data['awaiting_narx'] = True
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
        "/holat_ozgartir - lid holatini o'zgartir\n"
        "/hisobot - statistika\n"
        "/mijozlar - so'nggi mijozlar\n"
        "/yordam - shu menyu\n\n"
        "Narx kelishuvi:\n"
        "ha [narx] - mijozga narxni tasdiqla\n"
        "yo'q - yakuniy narxni yuboradi\n"
        "mijozga ayt [narx] - narx kutayotganga yuboradi"
    )


def main():
    global follow_ups
    follow_ups = _load_follow_ups()
    logger.info(f"Follow-ups yuklandi: {len(follow_ups)} ta")

    async def post_init(app: Application) -> None:
        global clients_db
        loaded = await asyncio.to_thread(sheets_load_mijozlar)
        if loaded:
            clients_db.update(loaded)
            logger.info(f"clients_db Mijozlar dan yuklandi: {len(loaded)} ta")
        asyncio.create_task(follow_up_checker(app.bot))

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("narx", cmd_narx))
    app.add_handler(CommandHandler("holat_ozgartir", cmd_holat_ozgartir))
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
