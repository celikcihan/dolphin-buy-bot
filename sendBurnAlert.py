import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TR_CHAT_ID = os.getenv("BUY_TR_CHAT_ID", "-1003950940276")
GLOBAL_CHAT_ID = os.getenv("BUY_GLOBAL_CHAT_ID", "-1003942696445")

BURN_AMOUNT = "50,000,000"
OLD_SUPPLY = "499,999,999"
NEW_SUPPLY = "449,999,999"
BURN_RESERVE_BEFORE = "90,000,000"
BURN_RESERVE_AFTER = "40,000,000"

TX_HASH = os.getenv("BURN_TX_HASH", "TX_HASH_BURAYA")
TX_URL = f"https://basescan.org/tx/{TX_HASH}"

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

msg_tr = f"""🔥 DOLPHIN YAKIM TAMAMLANDI 🔥

🐬 Yakılan Miktar:
{BURN_AMOUNT} DOLPHIN

📊 Eski Toplam Arz:
{OLD_SUPPLY} DOLPHIN

📉 Yeni Toplam Arz:
{NEW_SUPPLY} DOLPHIN

🔥 Yakım Rezervi:
{BURN_RESERVE_BEFORE} → {BURN_RESERVE_AFTER} DOLPHIN

🔒 LP:
Team Finance üzerinden 13 Haziran 2028'e kadar kilitli.

🔗 İşlem:
{TX_URL}

Şeffaflık ve uzun vadeli büyüme için çalışmaya devam ediyoruz. 🐬"""

msg_en = f"""🔥 DOLPHIN BURN COMPLETED 🔥

🐬 Burn Amount:
{BURN_AMOUNT} DOLPHIN

📊 Previous Total Supply:
{OLD_SUPPLY} DOLPHIN

📉 New Total Supply:
{NEW_SUPPLY} DOLPHIN

🔥 Burn Reserve:
{BURN_RESERVE_BEFORE} → {BURN_RESERVE_AFTER} DOLPHIN

🔒 LP:
Locked via Team Finance until June 13, 2028.

🔗 Transaction:
{TX_URL}

We continue building with transparency and long-term vision. 🐬"""

def send(chat_id, text):
    r = requests.post(TG_URL, json={
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }, timeout=30)
    r.raise_for_status()

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN boş.")

send(TR_CHAT_ID, msg_tr)
send(GLOBAL_CHAT_ID, msg_en)

print("Burn alert sent.")