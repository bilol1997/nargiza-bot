import asyncio
import logging
import os
from datetime import datetime

import pytz
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
BOSS_CHAT_ID = int(os.environ["BOSS_CHAT_ID"])

TASHKENT = pytz.timezone("Asia/Tashkent")

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
last_price_message: str | None = None


async def get_all_groups() -> list:
    """Nargiza a'zo bo'lgan barcha guruh va superguruhlarni qaytaradi."""
    groups = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, (Chat, Channel)):
            # Channel bo'lsa: broadcast kanallarni o'tkazib yuborish, faqat guruhlar
            if isinstance(entity, Channel) and entity.broadcast:
                continue
            groups.append(dialog)
    return groups


@client.on(events.NewMessage(from_users=BOSS_CHAT_ID))
async def on_boss_message(event):
    global last_price_message
    text = event.message.text
    if text and text.strip():
        last_price_message = text.strip()
        logger.info("BOSS yangi narx xabari saqlandi.")


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
            await asyncio.sleep(1)  # flood limit dan saqlanish
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
            logger.info(
                f"[{now.strftime('%H:%M')}] Ish vaqti emas (09:00–22:00 Toshkent). "
                "E'lon yuborilmadi."
            )


async def main():
    await client.connect()
    if not await client.is_user_authorized():
        logger.error(
            "SESSION_STRING yaroqsiz! "
            "generate_session.py ni lokal kompyuterda ishlatib qayta oling."
        )
        return
    me = await client.get_me()
    logger.info(f"Userbot ishga tushdi: {me.first_name} (@{me.username})")

    groups = await get_all_groups()
    logger.info(f"Topilgan guruhlar ({len(groups)} ta):")
    for g in groups:
        logger.info(f"  - {g.name} (id={g.id})")

    await asyncio.gather(
        announcer(),
        client.run_until_disconnected(),
    )


asyncio.run(main())
