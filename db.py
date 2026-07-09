import logging
import os
from datetime import datetime, timezone
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
    soha: Optional[str] = None,
) -> Optional[int]:
    """ISSIQ_LID uchun: avval mijozni upsert qiladi, keyin buyurtma yozadi.
    Ketma-ket (sinxron) ishlaydi — FK xatosi bo'lmasligi uchun.
    Qaytaradi: yangi buyurtmaning id si (yoki None, xato bo'lsa)."""
    upsert_mijoz(
        chat_id,
        ism=ism,
        telefon=telefon,
        telegram_username=telegram_username,
        til=til,
        status="issiq",
        soha=soha,
    )
    return add_buyurtma(chat_id, marka, miqdor_str)


def add_buyurtma(chat_id: int, marka: str, miqdor_str: str) -> Optional[int]:
    """
    Issiq lid kelganda yoki narx tasdiqlanganda buyurtma qo'shadi.
    miqdor_str: "500 kg", "2 tonna", "2000", "?" — har qanday formatda bo'lishi mumkin.
    Qaytaradi: yangi qatorning id si (yoki None).
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
        res = _client().table("buyurtmalar").insert(data).execute()
        if res.data:
            return res.data[0].get("id")
        return None
    except Exception as e:
        logger.error(f"add_buyurtma xato ({chat_id}, {marka}): {e}")
        return None


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
        "qop":                 "Qop",
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
    grouped: dict = {}
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


# ── Buyurtma tasdiqlash / bekor qilish ────────────────────────────────────────

def tasdiqla_buyurtma(buyurtma_id: int, sotilgan_miqdor: float) -> dict:
    """'<id> sotildi [miqdor]' — buyurtmani sotildi deb belgilaydi."""
    try:
        res = _client().table("buyurtmalar").select("*").eq("id", buyurtma_id).execute()
        if not res.data:
            return {"ok": False, "sabab": "topilmadi"}
        row = res.data[0]
        if row["status"] != "kutilmoqda":
            return {"ok": False, "sabab": "allaqachon_tasdiqlangan", "status": row["status"]}
        now_iso = datetime.now(timezone.utc).isoformat()
        _client().table("buyurtmalar").update({
            "status": "sotildi",
            "sotilgan_miqdor": sotilgan_miqdor if sotilgan_miqdor else row.get("miqdor"),
            "tasdiqlangan_sana": now_iso,
        }).eq("id", buyurtma_id).execute()
        return {"ok": True, "marka": row["marka"], "mijoz_id": row["mijoz_id"]}
    except Exception as e:
        logger.error(f"tasdiqla_buyurtma xato ({buyurtma_id}): {e}")
        return {"ok": False, "sabab": str(e)}


def bekor_qil_buyurtma(buyurtma_id: int, sabab: str = "") -> dict:
    """'<id> sotilmadi [sabab]' — buyurtmani bekor_qilindi deb belgilaydi."""
    try:
        res = _client().table("buyurtmalar").select("*").eq("id", buyurtma_id).execute()
        if not res.data:
            return {"ok": False, "sabab_xato": "topilmadi"}
        row = res.data[0]
        if row["status"] != "kutilmoqda":
            return {"ok": False, "sabab_xato": "allaqachon_tasdiqlangan", "status": row["status"]}
        update_data: dict = {"status": "bekor_qilindi"}
        if sabab:
            update_data["bekor_sababi"] = sabab
        _client().table("buyurtmalar").update(update_data).eq("id", buyurtma_id).execute()
        return {"ok": True, "marka": row["marka"], "mijoz_id": row["mijoz_id"]}
    except Exception as e:
        logger.error(f"bekor_qil_buyurtma xato ({buyurtma_id}): {e}")
        return {"ok": False, "sabab_xato": str(e)}


def get_bugungi_sotuv() -> list:
    """Bugungi sotilgan markalar va jami miqdor."""
    try:
        return _client().table("bugungi_sotuv").select("*").execute().data or []
    except Exception as e:
        logger.error(f"get_bugungi_sotuv xato: {e}")
        return []


def get_haftalik_sotuv() -> list:
    """So'nggi 7 kunlik sotuv hisoboti."""
    try:
        return _client().table("haftalik_sotuv").select("*").execute().data or []
    except Exception as e:
        logger.error(f"get_haftalik_sotuv xato: {e}")
        return []


def get_oylik_sotuv() -> list:
    """So'nggi 30 kunlik sotuv hisoboti."""
    try:
        return _client().table("oylik_sotuv").select("*").execute().data or []
    except Exception as e:
        logger.error(f"get_oylik_sotuv xato: {e}")
        return []


def get_mijoz(chat_id: int) -> Optional[dict]:
    """Supabase dan mijoz ma'lumotini oladi. Topilmasa None qaytaradi."""
    try:
        res = _client().table("mijozlar").select("*").eq("chat_id", chat_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_mijoz xato ({chat_id}): {e}")
        return None


def get_oxirgi_buyurtma(chat_id: int) -> Optional[dict]:
    """Mijozning eng so'nggi SOTILGAN buyurtmasini qaytaradi.
    kutilmoqda/bekor_qilindi holatlari hisobga olinmaydi."""
    try:
        res = (
            _client().table("buyurtmalar")
            .select("marka, miqdor, birlik, tasdiqlangan_sana")
            .eq("mijoz_id", chat_id)
            .eq("status", "sotildi")
            .order("tasdiqlangan_sana", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_oxirgi_buyurtma xato ({chat_id}): {e}")
        return None


def add_etiroz(
    mijoz_id: int,
    etiroz_turi: str,
    etiroz_matni: str = "",
    buyurtma_id: Optional[int] = None,
    raqib_nomi: Optional[str] = None,
    raqib_narxi: Optional[float] = None,
) -> None:
    valid = {
        "narx_baland", "boshqa_joyda_arzon",
        "hozir_kerak_emas", "boshqa_servis_yaxshi", "boshqa"
    }
    tur = etiroz_turi.lower().strip()
    if tur not in valid:
        tur = "boshqa"
    data: dict = {"mijoz_id": mijoz_id, "etiroz_turi": tur}
    if etiroz_matni:
        data["etiroz_matni"] = etiroz_matni
    if buyurtma_id:
        data["buyurtma_id"] = buyurtma_id
    if raqib_nomi:
        data["raqib_nomi"] = raqib_nomi
    if raqib_narxi is not None:
        data["raqib_narxi"] = raqib_narxi
    try:
        _client().table("etirozlar").insert(data).execute()
    except Exception as e:
        logger.error(f"add_etiroz xato ({mijoz_id}): {e}")


def get_etirozlar_taqsimoti() -> list:
    """etirozlar_taqsimoti VIEW dan bugungi/haftalik/oylik taqsimot."""
    try:
        return _client().table("etirozlar_taqsimoti").select("*").execute().data or []
    except Exception as e:
        logger.error(f"get_etirozlar_taqsimoti xato: {e}")
        return []


def get_kutilmoqda_buyurtmalar() -> list:
    """Kunlik hisobot uchun: status='kutilmoqda' bo'lgan barcha buyurtmalar."""
    try:
        res = (
            _client().table("buyurtmalar")
            .select("id, mijoz_id, marka, miqdor, birlik, sana, mijozlar(ism, telefon)")
            .eq("status", "kutilmoqda")
            .order("sana")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_kutilmoqda_buyurtmalar xato: {e}")
        return []


# ── Narxlar ───────────────────────────────────────────────────────────────────

def get_all_narxlar() -> dict:
    """Barcha joriy narxlarni {marka: narx} ko'rinishida qaytaradi."""
    try:
        res = _client().table("narxlar").select("marka, narx").execute()
        return {row["marka"]: row["narx"] for row in res.data}
    except Exception as e:
        logger.error(f"get_all_narxlar xato: {e}")
        return {}


def upsert_narx(marka: str, narx: float) -> None:
    """Bitta mahsulot narxini Supabase'da yangilaydi yoki qo'shadi."""
    try:
        _client().table("narxlar").upsert(
            {"marka": marka, "narx": narx, "yangilangan_vaqt": datetime.now(timezone.utc).isoformat()},
            on_conflict="marka",
        ).execute()
    except Exception as e:
        logger.error(f"upsert_narx xato ({marka}): {e}")
