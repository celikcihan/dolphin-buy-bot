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

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

PROJECT_NAME = os.getenv("PROJECT_NAME", "DOLPHIN")
CHAIN = os.getenv("CHAIN", "base")
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", "").lower()
PAIR_ADDRESS = os.getenv("PAIR_ADDRESS", "").lower()
TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", "18"))

BUY_TR_CHAT_ID = os.getenv("BUY_TR_CHAT_ID", "")
BUY_GLOBAL_CHAT_ID = os.getenv("BUY_GLOBAL_CHAT_ID", "")
SELL_CHAT_ID = os.getenv("SELL_CHAT_ID", "")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "20"))
MAX_BLOCK_RANGE = int(os.getenv("MAX_BLOCK_RANGE", "250"))
MIN_BUY_ALERT_USD = float(os.getenv("MIN_BUY_ALERT_USD", "1"))
MIN_SELL_ALERT_USD = float(os.getenv("MIN_SELL_ALERT_USD", "1"))

BUY_MEDIA_FILE = os.getenv("BUY_MEDIA_FILE", "dolphin_logo.jpg")
SELL_MEDIA_FILE = os.getenv("SELL_MEDIA_FILE", "dolphin_logo.jpg")
SEND_MEDIA = os.getenv("SEND_MEDIA", "true").lower() == "true"

HOLDERS_COUNT = os.getenv("HOLDERS_COUNT", "")

DEX_ADDRESSES_ENV = os.getenv("DEX_ADDRESSES", PAIR_ADDRESS)
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
session.headers.update({"User-Agent": "DOLPHIN-BUY-SELL-BOT/1.0"})

seen_hashes: Set[str] = set()
cached_pair: Optional[Dict[str, Any]] = None
last_pair_refresh = 0.0
last_checked_block: Optional[int] = None


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


def tg_post(method: str, payload: Dict[str, Any], files: Optional[Dict[str, Any]] = None) -> None:
    url = f"{TG_BASE}/bot{BOT_TOKEN}/{method}"

    if files:
        r = session.post(url, data=payload, files=files, timeout=30)
    else:
        r = session.post(url, json=payload, timeout=30)

    r.raise_for_status()


def send_telegram(text: str, chat_id: str, event_type: str) -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN boş.")
    if not chat_id:
        return

    media_file = BUY_MEDIA_FILE if event_type == "buy" else SELL_MEDIA_FILE

    if SEND_MEDIA and media_file and os.path.exists(media_file):
        with open(media_file, "rb") as f:
            payload = {
                "chat_id": chat_id,
                "caption": text,
                "disable_web_page_preview": "true",
            }
            files = {"photo": f}
            tg_post("sendPhoto", payload, files=files)
        return

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
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


def choose_pair(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not pairs:
        raise ValueError("DexScreener pair bulunamadı.")

    if PAIR_ADDRESS:
        for p in pairs:
            if str(p.get("pairAddress", "")).lower() == PAIR_ADDRESS:
                return p

    def score(p: Dict[str, Any]) -> float:
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)
        return liq * 1000 + vol

    return sorted(pairs, key=score, reverse=True)[0]


def refresh_pair() -> Dict[str, Any]:
    global cached_pair
    global last_pair_refresh

    now = time.time()

    if cached_pair and now - last_pair_refresh < 60:
        return cached_pair

    pairs = get_token_pairs()
    cached_pair = choose_pair(pairs)
    last_pair_refresh = now

    logger.info(
        "Pair yenilendi: %s | dex=%s | price=%s | liq=%s",
        cached_pair.get("pairAddress"),
        cached_pair.get("dexId"),
        cached_pair.get("priceUsd"),
        (cached_pair.get("liquidity") or {}).get("usd"),
    )

    return cached_pair


def get_holder_count() -> Optional[int]:
    try:
        if HOLDERS_COUNT:
            return int(float(HOLDERS_COUNT))
    except Exception:
        return None
    return None


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
        logs = get_all_transfer_logs_single(cursor, chunk_to)
        all_logs.extend(logs)
        cursor = chunk_to + 1

    return all_logs


def decode_transfer_log(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    topics = log.get("topics") or []

    if len(topics) < 3:
        return None

    from_addr = normalize_topic_address(topics[1])
    to_addr = normalize_topic_address(topics[2])
    raw_value = hex_to_int(log.get("data", "0x0"))
    token_amount = raw_value / (10 ** TOKEN_DECIMALS)

    return {
        "tx_hash": log.get("transactionHash", ""),
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


def find_wallet(tx_transfers: List[Dict[str, Any]], event_type: str, selected_transfer: Dict[str, Any]) -> str:
    if event_type == "buy":
        candidates = []
        for t in tx_transfers:
            to_addr = str(t.get("to", "")).lower()
            amount = float(t.get("token_amount") or 0)
            if to_addr in DEX_ADDRESSES or to_addr in IGNORE_ADDRESSES:
                continue
            candidates.append((to_addr, amount))

        if candidates:
            return max(candidates, key=lambda x: x[1])[0]

        return str(selected_transfer.get("to", "")).lower()

    candidates = []
    for t in tx_transfers:
        from_addr = str(t.get("from", "")).lower()
        amount = float(t.get("token_amount") or 0)
        if from_addr in DEX_ADDRESSES or from_addr in IGNORE_ADDRESSES:
            continue
        candidates.append((from_addr, amount))

    if candidates:
        return max(candidates, key=lambda x: x[1])[0]

    return str(selected_transfer.get("from", "")).lower()


def build_message_en(
    pair: Dict[str, Any],
    transfer: Dict[str, Any],
    event_type: str,
    wallet: str,
    wallet_balance: Optional[float],
    holders: Optional[int],
) -> str:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    base_symbol = base.get("symbol", PROJECT_NAME)
    quote_symbol = quote.get("symbol", "USDC")
    dex_id = pair.get("dexId", "DEX")
    chart_url = pair.get("url", "")

    price_usd = float(pair.get("priceUsd") or 0)
    price_native = float(pair.get("priceNative") or 0)

    liquidity_usd = (pair.get("liquidity") or {}).get("usd")
    market_cap = pair.get("marketCap")
    tx_hash = transfer.get("tx_hash", "")
    token_amount = float(transfer.get("token_amount") or 0)

    usd_value = token_amount * price_usd
    quote_amount = token_amount * price_native

    if event_type == "buy":
        title = f"🟢 {PROJECT_NAME} BUY!"
        lines = [
            title,
            "",
            f"💵 Spent: {quote_amount:,.6f} {quote_symbol} ({fmt_money(usd_value)})",
            f"🐬 Got: {fmt_number(token_amount)} {base_symbol}",
            f"📈 Price: {fmt_money(price_usd)}",
        ]
    else:
        title = f"🔴 {PROJECT_NAME} SELL!"
        lines = [
            title,
            "",
            f"🐬 Sold: {fmt_number(token_amount)} {base_symbol}",
            f"💰 Value: {quote_amount:,.6f} {quote_symbol} ({fmt_money(usd_value)})",
            f"📉 Price: {fmt_money(price_usd)}",
        ]

    if wallet_balance is not None:
        wallet_value = wallet_balance * price_usd
        lines.append(f"👤 Holdings: {fmt_number(wallet_balance)} {base_symbol} ({fmt_money(wallet_value)})")

    if holders is not None:
        lines.append(f"👥 Holders: {holders:,}")

    lines.append(f"🏪 DEX: {dex_id}")

    if liquidity_usd is not None:
        lines.append(f"💧 Liquidity: {fmt_money(float(liquidity_usd))}")

    if market_cap is not None:
        lines.append(f"🏦 Market Cap: {fmt_money(float(market_cap))}")

    lines.extend([
        "",
        f"👛 Wallet: {short_wallet(wallet)}",
        f"🔗 TX: https://basescan.org/tx/{tx_hash}",
    ])

    if chart_url:
        lines.append(f"📊 Chart: {chart_url}")

    return "\n".join(lines)


def build_message_tr(
    pair: Dict[str, Any],
    transfer: Dict[str, Any],
    wallet: str,
    wallet_balance: Optional[float],
    holders: Optional[int],
) -> str:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    base_symbol = base.get("symbol", PROJECT_NAME)
    quote_symbol = quote.get("symbol", "USDC")
    dex_id = pair.get("dexId", "DEX")
    chart_url = pair.get("url", "")

    price_usd = float(pair.get("priceUsd") or 0)
    price_native = float(pair.get("priceNative") or 0)

    liquidity_usd = (pair.get("liquidity") or {}).get("usd")
    market_cap = pair.get("marketCap")
    tx_hash = transfer.get("tx_hash", "")
    token_amount = float(transfer.get("token_amount") or 0)

    usd_value = token_amount * price_usd
    quote_amount = token_amount * price_native

    lines = [
        f"🟢 {PROJECT_NAME} ALIM!",
        "",
        f"💵 Harcanan: {quote_amount:,.6f} {quote_symbol} ({fmt_money(usd_value)})",
        f"🐬 Alınan: {fmt_number(token_amount)} {base_symbol}",
        f"📈 Fiyat: {fmt_money(price_usd)}",
    ]

    if wallet_balance is not None:
        wallet_value = wallet_balance * price_usd
        lines.append(f"👤 Cüzdan Bakiyesi: {fmt_number(wallet_balance)} {base_symbol} ({fmt_money(wallet_value)})")

    if holders is not None:
        lines.append(f"👥 Holder: {holders:,}")

    lines.append(f"🏪 DEX: {dex_id}")

    if liquidity_usd is not None:
        lines.append(f"💧 Likidite: {fmt_money(float(liquidity_usd))}")

    if market_cap is not None:
        lines.append(f"🏦 Market Cap: {fmt_money(float(market_cap))}")

    lines.extend([
        "",
        f"👛 Cüzdan: {short_wallet(wallet)}",
        f"🔗 TX: https://basescan.org/tx/{tx_hash}",
    ])

    if chart_url:
        lines.append(f"📊 Grafik: {chart_url}")

    return "\n".join(lines)


def process_transfers(transfers: List[Dict[str, Any]], pair: Dict[str, Any], holders: Optional[int]) -> None:
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
            if event_type:
                classified_items.append((event_type, transfer))

        if not classified_items:
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

        price_usd = float(pair.get("priceUsd") or 0)
        token_amount = float(selected_transfer.get("token_amount") or 0)
        usd_value = token_amount * price_usd

        if event_type == "buy" and usd_value < MIN_BUY_ALERT_USD:
            seen_hashes.add(tx_hash)
            continue

        if event_type == "sell" and usd_value < MIN_SELL_ALERT_USD:
            seen_hashes.add(tx_hash)
            continue

        wallet = find_wallet(tx_transfers, event_type, selected_transfer)
        wallet_balance = get_wallet_token_balance(wallet)

        if event_type == "buy":
            msg_tr = build_message_tr(pair, selected_transfer, wallet, wallet_balance, holders)
            msg_en = build_message_en(pair, selected_transfer, "buy", wallet, wallet_balance, holders)

            send_telegram(msg_tr, BUY_TR_CHAT_ID, "buy")
            send_telegram(msg_en, BUY_GLOBAL_CHAT_ID, "buy")
            logger.info("BUY alert gönderildi: %s", tx_hash)

        else:
            if SELL_CHAT_ID:
                msg_sell = build_message_en(pair, selected_transfer, "sell", wallet, wallet_balance, holders)
                send_telegram(msg_sell, SELL_CHAT_ID, "sell")
                logger.info("SELL alert gönderildi: %s", tx_hash)
            else:
                logger.info("SELL görüldü ama SELL_CHAT_ID boş: %s", tx_hash)

        seen_hashes.add(tx_hash)


def main() -> None:
    global last_checked_block

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN boş.")
    if not TOKEN_ADDRESS:
        raise ValueError("TOKEN_ADDRESS boş.")
    if not DEX_ADDRESSES:
        raise ValueError("DEX_ADDRESSES veya PAIR_ADDRESS boş.")

    logger.info("DOLPHIN BUY/SELL BOT başladı.")
    logger.info("Token: %s", TOKEN_ADDRESS)
    logger.info("Pair: %s", PAIR_ADDRESS)
    logger.info("DEX_ADDRESSES: %s", ",".join(sorted(DEX_ADDRESSES)))

    latest_block = get_latest_block()
    last_checked_block = latest_block

    logger.info("Başlangıç block: %s", last_checked_block)

    while True:
        try:
            latest_block = get_latest_block()

            from_block = (last_checked_block or latest_block) + 1
            to_block = latest_block

            if from_block > to_block:
                time.sleep(CHECK_INTERVAL)
                continue

            logger.info("Loop | from=%s | to=%s", from_block, to_block)

            pair = refresh_pair()
            holders = get_holder_count()

            logs = get_all_transfer_logs_chunked(from_block, to_block)

            transfers: List[Dict[str, Any]] = []

            for log in logs:
                decoded = decode_transfer_log(log)
                if decoded:
                    transfers.append(decoded)

            logger.info("Transfer log sayısı: %s", len(transfers))

            process_transfers(transfers, pair, holders)

            last_checked_block = to_block

            if len(seen_hashes) > 10000:
                seen_hashes.clear()

        except Exception as e:
            logger.exception("BUY/SELL BOT hata verdi: %s", e)

            try:
                latest_block = get_latest_block()
                last_checked_block = latest_block
                logger.warning("Hata sonrası latest block'a geçildi: %s", last_checked_block)
            except Exception:
                pass

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()