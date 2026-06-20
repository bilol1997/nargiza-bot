import logging
import os
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logger = logging.getLogger(__name__)

_url: str = os.environ.get("SUPABASE_URL", "")
_key: str = os.environ.get("SUPABASE_KEY", "")

_sb: Optional[Client] = None


def _client() -> Client:
    global _sb
    if _sb is None:
        if not _url or not _key:
            raise RuntimeError("SUPABASE_URL yoki SUPABASE_KEY .env da yo'q")
        _sb = create_client(_url, _key)
    return _sb


# ── Mijozlar ──────────────────────────────────────────────────────────────────

def upsert_mijoz(
    chat_id: int,
    *,
    ism: str = "",
    telefon: str = "",
    telegram_username: str = "",
    til: str = "",
    soha: Optional[str] = None,
    soha_aniqlangan_avtomatik: bool = True,
    status: str = "sovuq",
) -> None:
    """Yangi mijozni qo'shadi yoki mavjudini yangilaydi (chat_id asosida)."""
    data: dict = {"chat_id": chat_id}
    if ism:
        data["ism"] = ism
    if telefon:
        data["telefon"] = telefon
    if telegram_username:
        data["telegram_username"] = telegram_username
    if til:
        data["til"] = til
    if soha:
        data["soha"] = soha
        data["soha_aniqlangan_avtomatik"] = soha_aniqlangan_avtomatik
    if status:
        data["status"] = status
    try:
        _client().table("mijozlar").upsert(data, on_conflict="chat_id").execute()
    except Exception as e:
        logger.error(f"upsert_mijoz xato ({chat_id}): {e}")


def update_mijoz_status(chat_id: int, status: str) -> None:
    """Mijoz statusini yangilaydi: sovuq→issiq→birinchi_sotuv→doimiy..."""
    try:
        _client().table("mijozlar").update({"status": status}).eq("chat_id", chat_id).execute()
    except Exception as e:
        logger.error(f"update_mijoz_status xato ({chat_id}): {e}")


def update_mijoz_ism(chat_id: int, ism: str) -> None:
    try:
        _client().table("mijozlar").update({"ism": ism}).eq("chat_id", chat_id).execute()
    except Exception as e:
        logger.error(f"update_mijoz_ism xato ({chat_id}): {e}")


def update_mijoz_telefon(chat_id: int, telefon: str) -> None:
    try:
        _client().table("mijozlar").update({"telefon": telefon}).eq("chat_id", chat_id).execute()
    except Exception as e:
        logger.error(f"update_mijoz_telefon xato ({chat_id}): {e}")


# ── Buyurtmalar ───────────────────────────────────────────────────────────────

def upsert_mijoz_va_buyurtma(
    chat_id: int,
    ism: str,
    telefon: str,
    telegram_username: str,
    til: str,
    marka: str,
    miqdor_str: str,
) -> None:
    """ISSIQ_LID uchun: avval mijozni upsert qiladi, keyin buyurtma yozadi.
    Ketma-ket (sinxron) ishlaydi — FK xatosi bo'lmasligi uchun."""
    upsert_mijoz(
        chat_id,
        ism=ism,
        telefon=telefon,
        telegram_username=telegram_username,
        til=til,
        status="issiq",
    )
    add_buyurtma(chat_id, marka, miqdor_str)


def add_buyurtma(chat_id: int, marka: str, miqdor_str: str) -> None:
    """
    Issiq lid kelganda yoki narx tasdiqlanganda buyurtma qo'shadi.
    miqdor_str: "500 kg", "2 tonna", "2000", "?" — har qanday formatda bo'lishi mumkin.
    """
    miqdor, birlik = _parse_miqdor(miqdor_str)
    data: dict = {
        "mijoz_id": chat_id,
        "marka": marka,
        "birlik": birlik,
    }
    if miqdor is not None:
        data["miqdor"] = miqdor
    try:
        _client().table("buyurtmalar").insert(data).execute()
    except Exception as e:
        logger.error(f"add_buyurtma xato ({chat_id}, {marka}): {e}")


def _parse_miqdor(raw: str) -> tuple:
    """'500 kg' → (500.0, 'kg'),  '2 tonna' → (2000.0, 'kg'),  '?' → (None, 'kg')"""
    import re
    if not raw or raw.strip() == "?":
        return None, "kg"
    low = raw.lower().replace(",", ".")
    m = re.search(r"[\d.]+", low)
    if not m:
        return None, "kg"
    val = float(m.group())
    if "tonna" in low or " t" in low:
        return val * 1000, "kg"
    return val, "kg"


# ── BOSS: /mijozlar ro'yxati ──────────────────────────────────────────────────

def get_boss_mijozlar() -> list[dict]:
    """
    boss_mijozlar_royxati VIEW dan ma'lumot oladi.
    Qaytaradi: soha → status tartibida saralangan ro'yxat.
    """
    try:
        res = _client().table("boss_mijozlar_royxati").select("*").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"get_boss_mijozlar xato: {e}")
        return []


def format_boss_mijozlar(rows: list[dict]) -> str:
    """VIEW natijasini BOSS uchun chiroyli matn formatiga o'giradi."""
    if not rows:
        return "Hozircha mijozlar yo'q."

    SOHA_NOMI = {
        "issiqxona_plyonkasi": "Issiqxona plyonkasi",
        "paket":               "Paket",
        "qopchiq":             "Qopchiq",
        "oyinchoq_idish":      "O'yinchoq / idish",
        "quvur":               "Quvur",
        "kabel":               "Kabel",
        "bir_martalik_idish":  "Bir martalik idish",
        "boshqa":              "Boshqa",
        None:                  "Soha aniqlanmagan",
    }

    STATUS_NOMI = {
        "sovuq":          "Sovuq",
        "issiq":          "Issiq",
        "birinchi_sotuv": "Birinchi sotuv",
        "doimiy":         "Doimiy",
        "uxlab_qoldi":    "Uxlab qoldi",
    }

    # Soha bo'yicha guruhlash
    grouped: dict[str | None, list[dict]] = {}
    for row in rows:
        soha = row.get("soha")
        grouped.setdefault(soha, []).append(row)

    lines = [f"Mijozlar ro'yxati — jami {len(rows)} ta\n"]
    for soha, members in grouped.items():
        lines.append(f"\n{'─' * 30}")
        noma_lum = soha or "Noma'lum"
        lines.append(f"📦 {SOHA_NOMI.get(soha, noma_lum)}")
        lines.append(f"{'─' * 30}")
        for m in members:
            status_label = STATUS_NOMI.get(m.get("status"), m.get("status") or "?")
            ism      = m.get("ism") or "Noma'lum"
            tel      = m.get("telefon") or "—"
            markalar = m.get("markalar") or "—"
            hajm_kg  = m.get("oylik_hajm_kg") or 0
            hajm_str = f"{int(hajm_kg):,} kg".replace(",", " ") if hajm_kg else "—"
            tg       = m.get("telegram_username") or ""

            lines.append(
                f"\n• {ism}{' ' + tg if tg else ''} [{status_label}]\n"
                f"  Tel: {tel}\n"
                f"  Markalar: {markalar}\n"
                f"  Oylik hajm: {hajm_str}"
            )

    return "\n".join(lines)
