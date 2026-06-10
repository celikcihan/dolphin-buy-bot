#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
import time
import logging
from typing import Any, Dict, List, Optional, Set

import requests


DEX_BASE = "https://api.dexscreener.com"
TG_BASE = "https://api.telegram.org"
MORALIS_BASE = "https://deep-index.moralis.io/api/v2.2"

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Channel setup
# GLOBAL_CHANNEL_ID: English public/global channel
# TR_CHANNEL_ID: Turkish channel
# CHANNEL_ID and SELL_CHANNEL_ID are kept as backward-compatible fallbacks.
GLOBAL_CHANNEL_ID = os.getenv("GLOBAL_CHANNEL_ID", os.getenv("CHANNEL_ID", ""))
TR_CHANNEL_ID = os.getenv("TR_CHANNEL_ID", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", GLOBAL_CHANNEL_ID)
SELL_CHANNEL_ID = os.getenv("SELL_CHANNEL_ID", GLOBAL_CHANNEL_ID or CHANNEL_ID)


PROJECT_NAME = os.getenv("PROJECT_NAME", "DOLPHIN")

CHAIN = os.getenv("CHAIN", "base")
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", "0x985fE8B33da494d7fE595B054590d9Eac576b2d1").lower()
TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", "18"))

MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
HOLDERS_COUNT = os.getenv("HOLDERS_COUNT", "0")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "90"))
MIN_BUY_ALERT_USD = float(os.getenv("MIN_BUY_ALERT_USD", "1"))
MIN_SELL_ALERT_USD = float(os.getenv("MIN_SELL_ALERT_USD", "1"))

# Safety switch. Keep these false during your own early LP/price-lift buys.
BOT_ENABLED = os.getenv("BOT_ENABLED", "false").lower() == "true"
ANNOUNCE_BUYS = os.getenv("ANNOUNCE_BUYS", "false").lower() == "true"
ANNOUNCE_SELLS = os.getenv("ANNOUNCE_SELLS", "false").lower() == "true"

BLOCK_LOOKBACK = int(os.getenv("BLOCK_LOOKBACK", "3000"))
MAX_BLOCK_RANGE = int(os.getenv("MAX_BLOCK_RANGE", "500"))
CATCH_UP_MAX_BLOCKS = int(os.getenv("CATCH_UP_MAX_BLOCKS", "3000"))

PRICE_REFRESH_SECONDS = int(os.getenv("PRICE_REFRESH_SECONDS", "600"))
HOLDER_REFRESH_SECONDS = int(os.getenv("HOLDER_REFRESH_SECONDS", "1800"))

# Logo / media. BUY_MEDIA_URL and SELL_MEDIA_URL can be public HTTPS URL or Telegram file_id.
# BUY_MEDIA_MODE / SELL_MEDIA_MODE: text, photo, animation
BUY_MEDIA_URL = os.getenv("BUY_MEDIA_URL", "")
SELL_MEDIA_URL = os.getenv("SELL_MEDIA_URL", "")
BUY_MEDIA_MODE = os.getenv("BUY_MEDIA_MODE", "photo").lower()
SELL_MEDIA_MODE = os.getenv("SELL_MEDIA_MODE", "photo").lower()

# Add the actual pool/pair address after LP is created.
# Example: DEX_ADDRESSES=0xPoolAddress
DEX_ADDRESSES_ENV = os.getenv("DEX_ADDRESSES", "")

DEX_ADDRESSES = {
    x.strip().lower()
    for x in DEX_ADDRESSES_ENV.split(",")
    if x.strip()
}

IGNORE_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("dolphin-buy-sell-bot")

session = requests.Session()
session.headers.update({"User-Agent": "DOLPHIN-BUY-SELL-BOT/8.1-STABLE"})

seen_hashes: Set[str] = set()
cached_pair: Optional[Dict[str, Any]] = None
cached_holders: Optional[int] = None

last_checked_block: Optional[int] = None
last_price_refresh = 0.0
last_holder_refresh = 0.0


def rpc_call(method: str, params: list[Any]) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": method,
        "params": params,
    }

    r = session.post(BASE_RPC_URL, json=payload, timeout=30)
    r.raise_for_status()

    data = r.json()

    if "error" in data:
        raise RuntimeError(data["error"])

    return data.get("result")


def hex_to_int(value: str) -> int:
    return int(value, 16)


def int_to_hex(value: int) -> str:
    return hex(value)


def normalize_topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def escape_md(text: str) -> str:
    return str(text).replace("_", "\\_")


def fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1:
        return f"${v:,.2f}"
    return f"${v:.10f}"


def fmt_number(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1_000_000:
        return f"{v:,.0f}"
    if abs(v) >= 1_000:
        return f"{v:,.2f}"
    return f"{v:,.6f}"


def short_wallet(addr: str) -> str:
    if not addr:
        return "n/a"
    return f"{addr[:6]}...{addr[-4:]}"


def tg_post(method: str, payload: Dict[str, Any]) -> None:
    url = f"{TG_BASE}/bot{BOT_TOKEN}/{method}"
    r = session.post(url, json=payload, timeout=30)
    r.raise_for_status()


def send_telegram(text: str, chat_id: str, event_type: str) -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN boş.")
    if not chat_id:
        raise ValueError("CHANNEL_ID boş.")

    media_url = BUY_MEDIA_URL if event_type == "buy" else SELL_MEDIA_URL
    media_mode = BUY_MEDIA_MODE if event_type == "buy" else SELL_MEDIA_MODE

    if media_url and media_mode in {"photo", "image"}:
        payload = {
            "chat_id": chat_id,
            "photo": media_url,
            "caption": text,
            "disable_web_page_preview": True,
            "parse_mode": "Markdown",
        }
        tg_post("sendPhoto", payload)
        return

    if media_url and media_mode in {"animation", "gif"}:
        payload = {
            "chat_id": chat_id,
            "animation": media_url,
            "caption": text,
            "disable_web_page_preview": True,
            "parse_mode": "Markdown",
        }
        tg_post("sendAnimation", payload)
        return

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "Markdown",
    }
    tg_post("sendMessage", payload)


def get_latest_block() -> int:
    result = rpc_call("eth_blockNumber", [])
    return hex_to_int(result)


def get_token_pairs() -> List[Dict[str, Any]]:
    url = f"{DEX_BASE}/token-pairs/v1/{CHAIN}/{TOKEN_ADDRESS}"

    r = session.get(url, timeout=20)
    r.raise_for_status()

    data = r.json()

    if isinstance(data, list):
        return data

    return data.get("pairs", []) or []


def choose_best_pair(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not pairs:
        raise ValueError("DexScreener pair bulunamadı.")

    def score(p: Dict[str, Any]) -> float:
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)
        return liq * 1000 + vol

    return sorted(pairs, key=score, reverse=True)[0]


def get_pair() -> Dict[str, Any]:
    global cached_pair

    if cached_pair:
        return cached_pair

    pairs = get_token_pairs()
    pair = choose_best_pair(pairs)
    cached_pair = pair

    logger.info(
        "Otomatik pair seçildi: %s | dex=%s | liquidity=%s",
        pair.get("pairAddress"),
        pair.get("dexId"),
        (pair.get("liquidity") or {}).get("usd"),
    )

    return pair


def refresh_pair() -> Dict[str, Any]:
    global cached_pair
    global last_price_refresh

    now = time.time()

    if cached_pair and (now - last_price_refresh < PRICE_REFRESH_SECONDS):
        return cached_pair

    try:
        cached_pair = None
        pair = get_pair()
        cached_pair = pair
        last_price_refresh = now
        logger.info("Pair price yenilendi.")
        return pair
    except Exception as e:
        logger.warning("Pair refresh hata: %s", e)

        if cached_pair:
            return cached_pair

        raise


def get_holder_count() -> Optional[int]:
    global cached_holders
    global last_holder_refresh

    now = time.time()

    if cached_holders is not None and (now - last_holder_refresh < HOLDER_REFRESH_SECONDS):
        return cached_holders

    if MORALIS_API_KEY:
        try:
            url = f"{MORALIS_BASE}/erc20/{TOKEN_ADDRESS}/holders"

            headers = {
                "accept": "application/json",
                "X-API-Key": MORALIS_API_KEY,
            }

            params = {
                "chain": CHAIN,
            }

            r = session.get(url, headers=headers, params=params, timeout=20)
            r.raise_for_status()

            data = r.json()

            possible_keys = [
                "totalHolders",
                "total_holders",
                "holder_count",
                "holders_count",
                "holders",
            ]

            for key in possible_keys:
                value = data.get(key)

                if value is None:
                    continue

                try:
                    holders = int(float(value))
                    cached_holders = holders
                    last_holder_refresh = now

                    logger.info("Holders Moralis ile alındı: %s", holders)

                    return holders
                except Exception:
                    continue

            logger.warning("Moralis holders response parse edilemedi: %s", data)

        except Exception as e:
            logger.warning("Moralis holders hata: %s", e)

    try:
        fallback = int(float(HOLDERS_COUNT))

        if fallback > 0:
            cached_holders = fallback
            last_holder_refresh = now
            logger.info("Holders fallback kullanıldı: %s", fallback)
            return fallback
    except Exception:
        pass

    return cached_holders


def get_all_transfer_logs_single(from_block: int, to_block: int) -> List[Dict[str, Any]]:
    params = {
        "fromBlock": int_to_hex(from_block),
        "toBlock": int_to_hex(to_block),
        "address": TOKEN_ADDRESS,
        "topics": [TRANSFER_TOPIC],
    }

    result = rpc_call("eth_getLogs", [params])

    if not isinstance(result, list):
        return []

    return result


def get_all_transfer_logs_chunked(from_block: int, to_block: int) -> List[Dict[str, Any]]:
    all_logs: List[Dict[str, Any]] = []
    cursor = from_block

    while cursor <= to_block:
        chunk_to = min(cursor + MAX_BLOCK_RANGE - 1, to_block)

        try:
            logs = get_all_transfer_logs_single(cursor, chunk_to)
            all_logs.extend(logs)

            logger.info(
                "Chunk tarandı: %s -> %s | log=%s",
                cursor,
                chunk_to,
                len(logs),
            )

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in {413, 429}:
                logger.warning(
                    "RPC limit/hata. Chunk atlandı: %s -> %s | status=%s",
                    cursor,
                    chunk_to,
                    e.response.status_code,
                )
            else:
                raise

        cursor = chunk_to + 1

    return all_logs


def decode_transfer_log(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    topics = log.get("topics") or []

    if len(topics) < 3:
        return None

    from_addr = normalize_topic_address(topics[1])
    to_addr = normalize_topic_address(topics[2])

    raw_value_hex = log.get("data", "0x0")
    raw_value = hex_to_int(raw_value_hex)

    token_amount = raw_value / (10 ** TOKEN_DECIMALS)

    tx_hash = log.get("transactionHash", "")

    return {
        "tx_hash": tx_hash,
        "from": from_addr,
        "to": to_addr,
        "token_amount": token_amount,
        "block_number": hex_to_int(log.get("blockNumber", "0x0")),
    }


def classify_transfer(transfer: Dict[str, Any]) -> Optional[str]:
    from_addr = str(transfer.get("from", "")).lower()
    to_addr = str(transfer.get("to", "")).lower()

    if from_addr in IGNORE_ADDRESSES or to_addr in IGNORE_ADDRESSES:
        return None

    if from_addr in DEX_ADDRESSES and to_addr not in DEX_ADDRESSES:
        return "buy"

    if to_addr in DEX_ADDRESSES and from_addr not in DEX_ADDRESSES:
        return "sell"

    return None


def find_real_wallet(
    tx_transfers: List[Dict[str, Any]],
    event_type: str,
    selected_transfer: Dict[str, Any],
) -> str:
    if event_type == "buy":
        candidates = []

        for t in tx_transfers:
            to_addr = str(t.get("to", "")).lower()
            from_addr = str(t.get("from", "")).lower()
            amount = float(t.get("token_amount") or 0)

            if to_addr in IGNORE_ADDRESSES:
                continue

            if to_addr in DEX_ADDRESSES:
                continue

            candidates.append(
                {
                    "address": to_addr,
                    "amount": amount,
                    "from_is_dex": from_addr in DEX_ADDRESSES,
                }
            )

        final_candidates = [
            c for c in candidates
            if not c["from_is_dex"]
        ]

        if final_candidates:
            return max(final_candidates, key=lambda c: c["amount"])["address"]

        if candidates:
            return max(candidates, key=lambda c: c["amount"])["address"]

        return str(selected_transfer.get("to", "")).lower()

    candidates = []

    for t in tx_transfers:
        from_addr = str(t.get("from", "")).lower()
        to_addr = str(t.get("to", "")).lower()
        amount = float(t.get("token_amount") or 0)

        if from_addr in IGNORE_ADDRESSES:
            continue

        if from_addr in DEX_ADDRESSES:
            continue

        candidates.append(
            {
                "address": from_addr,
                "amount": amount,
                "to_is_dex": to_addr in DEX_ADDRESSES,
            }
        )

    to_dex_candidates = [
        c for c in candidates
        if c["to_is_dex"]
    ]

    if to_dex_candidates:
        return max(to_dex_candidates, key=lambda c: c["amount"])["address"]

    if candidates:
        return max(candidates, key=lambda c: c["amount"])["address"]

    return str(selected_transfer.get("from", "")).lower()


def get_wallet_token_balance(wallet: str) -> Optional[float]:
    selector = "0x70a08231"
    wallet_clean = wallet.lower().replace("0x", "").rjust(64, "0")
    data = selector + wallet_clean

    call_obj = {
        "to": TOKEN_ADDRESS,
        "data": data,
    }

    try:
        result = rpc_call("eth_call", [call_obj, "latest"])
        raw = hex_to_int(result)
        return raw / (10 ** TOKEN_DECIMALS)
    except Exception as e:
        logger.warning("Wallet balance alınamadı: %s", e)
        return None


def build_message(
    pair: Dict[str, Any],
    transfer: Dict[str, Any],
    event_type: str,
    wallet: str,
    wallet_balance: Optional[float],
    holders: Optional[int],
    language: str = "en",
) -> str:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    base_symbol = escape_md(base.get("symbol", PROJECT_NAME))
    quote_symbol = escape_md(quote.get("symbol", "ETH"))

    dex_id = escape_md(pair.get("dexId", "DEX"))
    chart_url = pair.get("url", "")

    price_usd = float(pair.get("priceUsd") or 0)
    price_native = float(pair.get("priceNative") or 0)

    liquidity_usd = (pair.get("liquidity") or {}).get("usd")
    market_cap = pair.get("marketCap")

    tx_hash = transfer.get("tx_hash", "")
    token_amount = float(transfer.get("token_amount") or 0)

    usd_value = token_amount * price_usd
    quote_amount = token_amount * price_native

    is_tr = language.lower().startswith("tr")
    lines: List[str] = []

    if event_type == "buy":
        lines.append(f"🐬🟢 {escape_md(PROJECT_NAME)} {'ALIM!' if is_tr else 'BUY!'}")
        lines.append("")
        if is_tr:
            lines.append(f"💵 Harcanan: {quote_amount:,.6f} {quote_symbol} ({fmt_money(usd_value)})")
            lines.append(f"🪙 Alınan: {fmt_number(token_amount)} {base_symbol}")
            lines.append(f"📈 Fiyat: {fmt_money(price_usd)}")
        else:
            lines.append(f"💵 Spent: {quote_amount:,.6f} {quote_symbol} ({fmt_money(usd_value)})")
            lines.append(f"🪙 Got: {fmt_number(token_amount)} {base_symbol}")
            lines.append(f"📈 Price: {fmt_money(price_usd)}")
    else:
        lines.append(f"🔴 {escape_md(PROJECT_NAME)} {'SATIŞ!' if is_tr else 'SELL!'}")
        lines.append("")
        if is_tr:
            lines.append(f"🪙 Satılan: {fmt_number(token_amount)} {base_symbol}")
            lines.append(f"💰 Değer: {quote_amount:,.6f} {quote_symbol} ({fmt_money(usd_value)})")
            lines.append(f"📉 Fiyat: {fmt_money(price_usd)}")
        else:
            lines.append(f"🪙 Sold: {fmt_number(token_amount)} {base_symbol}")
            lines.append(f"💰 Value: {quote_amount:,.6f} {quote_symbol} ({fmt_money(usd_value)})")
            lines.append(f"📉 Price: {fmt_money(price_usd)}")

    if wallet_balance is not None:
        wallet_value = wallet_balance * price_usd
        label = "Cüzdan Bakiyesi" if is_tr else "Holdings"
        lines.append(f"👤 {label}: {fmt_number(wallet_balance)} {base_symbol} ({fmt_money(wallet_value)})")

    if holders is not None:
        lines.append(f"👥 Holders: {holders:,}")

    lines.append(f"🏪 DEX: {dex_id}")

    if liquidity_usd is not None:
        label = "Likidite" if is_tr else "Liquidity"
        lines.append(f"💧 {label}: {fmt_money(float(liquidity_usd))}")

    if market_cap is not None:
        label = "Market Değeri" if is_tr else "Market Cap"
        lines.append(f"🏦 {label}: {fmt_money(float(market_cap))}")

    lines.append("")
    lines.append(f"👛 Wallet: `{wallet}`")
    lines.append(f"🔗 [TX](https://basescan.org/tx/{tx_hash})")

    if chart_url:
        lines.append(f"📊 [Chart]({chart_url})")

    return "\n".join(lines)


def send_event_to_channels(
    pair: Dict[str, Any],
    transfer: Dict[str, Any],
    event_type: str,
    wallet: str,
    wallet_balance: Optional[float],
    holders: Optional[int],
) -> None:
    """Send one detected event to configured channels.

    Buy messages can use animation/photo. Sell messages default to text if SELL_MEDIA_MODE=text.
    Global channel receives English; TR channel receives Turkish.
    """
    if event_type == "buy" and not ANNOUNCE_BUYS:
        logger.info("BUY algılandı ama ANNOUNCE_BUYS=false: %s", transfer.get("tx_hash"))
        return

    if event_type == "sell" and not ANNOUNCE_SELLS:
        logger.info("SELL algılandı ama ANNOUNCE_SELLS=false: %s", transfer.get("tx_hash"))
        return

    sent_any = False

    if GLOBAL_CHANNEL_ID:
        msg_en = build_message(
            pair=pair,
            transfer=transfer,
            event_type=event_type,
            wallet=wallet,
            wallet_balance=wallet_balance,
            holders=holders,
            language="en",
        )
        send_telegram(msg_en, GLOBAL_CHANNEL_ID, event_type)
        sent_any = True
        logger.info("%s alert Global kanala gönderildi: %s", event_type.upper(), transfer.get("tx_hash"))

    if TR_CHANNEL_ID:
        msg_tr = build_message(
            pair=pair,
            transfer=transfer,
            event_type=event_type,
            wallet=wallet,
            wallet_balance=wallet_balance,
            holders=holders,
            language="tr",
        )
        send_telegram(msg_tr, TR_CHANNEL_ID, event_type)
        sent_any = True
        logger.info("%s alert TR kanala gönderildi: %s", event_type.upper(), transfer.get("tx_hash"))

    if not sent_any:
        fallback_channel = CHANNEL_ID if event_type == "buy" else SELL_CHANNEL_ID
        msg = build_message(
            pair=pair,
            transfer=transfer,
            event_type=event_type,
            wallet=wallet,
            wallet_balance=wallet_balance,
            holders=holders,
            language="en",
        )
        send_telegram(msg, fallback_channel, event_type)
        logger.info("%s alert fallback kanala gönderildi: %s", event_type.upper(), transfer.get("tx_hash"))

def process_transfers(
    transfers: List[Dict[str, Any]],
    pair: Dict[str, Any],
    holders: Optional[int],
) -> None:
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for transfer in transfers:
        tx_hash = transfer.get("tx_hash")

        if not tx_hash:
            continue

        grouped.setdefault(tx_hash, []).append(transfer)

    for tx_hash, tx_transfers in grouped.items():
        if tx_hash in seen_hashes:
            continue

        classified_items = []

        for transfer in tx_transfers:
            event_type = classify_transfer(transfer)

            if event_type is None:
                logger.info(
                    "TRANSFER DEBUG | tx=%s | from=%s | to=%s | amount=%s",
                    tx_hash,
                    transfer.get("from"),
                    transfer.get("to"),
                    fmt_number(float(transfer.get("token_amount") or 0)),
                )
                continue

            classified_items.append((event_type, transfer))

        if not classified_items:
            logger.info(
                "TX sınıflandırılamadı: %s | transfer_count=%s",
                tx_hash,
                len(tx_transfers),
            )
            continue

        buy_items = [item for item in classified_items if item[0] == "buy"]
        sell_items = [item for item in classified_items if item[0] == "sell"]

        if buy_items:
            event_type, selected_transfer = max(
                buy_items,
                key=lambda x: float(x[1].get("token_amount") or 0),
            )
        elif sell_items:
            event_type, selected_transfer = max(
                sell_items,
                key=lambda x: float(x[1].get("token_amount") or 0),
            )
        else:
            continue

        token_amount = float(selected_transfer.get("token_amount") or 0)
        price_usd = float(pair.get("priceUsd") or 0)
        usd_value = token_amount * price_usd

        if event_type == "buy" and usd_value < MIN_BUY_ALERT_USD:
            logger.info("Buy küçük geçti: %s | %s", fmt_money(usd_value), tx_hash)
            seen_hashes.add(tx_hash)
            continue

        if event_type == "sell" and usd_value < MIN_SELL_ALERT_USD:
            logger.info("Sell küçük geçti: %s | %s", fmt_money(usd_value), tx_hash)
            seen_hashes.add(tx_hash)
            continue

        wallet = find_real_wallet(
            tx_transfers=tx_transfers,
            event_type=event_type,
            selected_transfer=selected_transfer,
        )

        wallet_balance = get_wallet_token_balance(wallet)

        if not BOT_ENABLED:
            logger.info(
                "PASIF MOD | %s algılandı ama Telegram'a gönderilmedi: %s | value=%s",
                event_type.upper(),
                tx_hash,
                fmt_money(usd_value),
            )
            seen_hashes.add(tx_hash)
            continue

        send_event_to_channels(
            pair=pair,
            transfer=selected_transfer,
            event_type=event_type,
            wallet=wallet,
            wallet_balance=wallet_balance,
            holders=holders,
        )

        seen_hashes.add(tx_hash)


def main() -> None:
    global last_checked_block
    global cached_pair
    global last_price_refresh

    logger.info("DOLPHIN BUY/SELL BOT RPC başladı.")
    logger.info("Bot version: DOLPHIN-BUY-SELL-BOT/1.2-HOLDER-READY")
    logger.info("BOT_ENABLED=%s | ANNOUNCE_BUYS=%s | ANNOUNCE_SELLS=%s", BOT_ENABLED, ANNOUNCE_BUYS, ANNOUNCE_SELLS)
    logger.info("GLOBAL_CHANNEL_ID=%s | TR_CHANNEL_ID=%s", GLOBAL_CHANNEL_ID or "Yok", TR_CHANNEL_ID or "Yok")
    logger.info("DEX adresleri: %s", ",".join(sorted(DEX_ADDRESSES)) if DEX_ADDRESSES else "Henüz girilmedi")

    latest_block = get_latest_block()
    last_checked_block = max(latest_block - BLOCK_LOOKBACK, 0)

    try:
        pair = get_pair()
        cached_pair = pair
        last_price_refresh = time.time()
    except Exception as e:
        logger.warning("Başlangıçta pair bulunamadı. LP oluşana kadar bot bekleme modunda kalacak: %s", e)

    logger.info("Başlangıç block: %s", last_checked_block)

    while True:
        try:
            latest_block = get_latest_block()

            from_block = (last_checked_block or latest_block) + 1
            to_block = latest_block

            lag = to_block - from_block + 1

            if lag > CATCH_UP_MAX_BLOCKS:
                from_block = max(to_block - CATCH_UP_MAX_BLOCKS + 1, 0)
                logger.warning(
                    "Çok büyük backlog kısaltıldı. Yeni aralık: %s -> %s",
                    from_block,
                    to_block,
                )

            logger.info(
                "Loop | latest_block=%s | from=%s | to=%s",
                latest_block,
                from_block,
                to_block,
            )

            if from_block > to_block:
                time.sleep(CHECK_INTERVAL)
                continue

            holders = get_holder_count()

            try:
                pair = refresh_pair()
            except Exception as e:
                logger.warning("Pair henüz bulunamadı. LP oluşunca otomatik devam edecek: %s", e)
                last_checked_block = to_block
                time.sleep(CHECK_INTERVAL)
                continue

            logs = get_all_transfer_logs_chunked(
                from_block=from_block,
                to_block=to_block,
            )

            transfers: List[Dict[str, Any]] = []

            for log in logs:
                decoded = decode_transfer_log(log)

                if decoded:
                    transfers.append(decoded)

            logger.info(
                "Block kontrol: %s -> %s | toplam DOLPHIN transfer log: %s",
                from_block,
                to_block,
                len(transfers),
            )

            process_transfers(
                transfers=transfers,
                pair=pair,
                holders=holders,
            )

            last_checked_block = to_block

            if len(seen_hashes) > 5000:
                seen_hashes.clear()

        except Exception as e:
            logger.exception("BUY/SELL BOT hata verdi: %s", e)

            try:
                latest_block = get_latest_block()
                last_checked_block = max(latest_block - CATCH_UP_MAX_BLOCKS, 0)
                logger.warning(
                    "Hata sonrası last_checked_block güncellendi: %s",
                    last_checked_block,
                )
            except Exception:
                pass

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
