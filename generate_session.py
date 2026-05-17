"""
Bu scriptni LOKAL kompyuterda bir marta ishlatng.
Telefon raqamingizni va SMS kodni kiriting — SESSION_STRING hosil bo'ladi.
Uni Railway da environment variable sifatida qo'shing.

Ishlatish:
    pip install telethon
    python generate_session.py
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API_ID (my.telegram.org dan): "))
api_hash = input("API_HASH (my.telegram.org dan): ")

print("\nTelegram akkauntingizga kirilmoqda...")
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n" + "=" * 60)
    print("SESSION_STRING (Railway ga qo'shing):")
    print("=" * 60)
    print(client.session.save())
    print("=" * 60)
