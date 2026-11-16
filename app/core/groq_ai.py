import json
import re
import time
import hashlib
import logging

import requests

from app.config import config
from app.core.utils import now_tz, parse_amount
from app.core.cache import groq_cache
from app.core.metrics import track_groq

logger = logging.getLogger(__name__)

def _extract_json(content: str) -> str:
    raw = re.sub(r"<think>[\s\S]*?</think>", "", str(content or ""), flags=re.I).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON tidak ditemukan pada respons Groq.")
    return raw[start:end + 1]


def _sales_prompt(today: str) -> str:
    products = "; ".join(
        f"{item['nama']} ({', '.join(item['kunci'])}; {item['satuan']})"
        for item in config.KATALOG
    )
    units = "; ".join(config.UNIT_USAHA)
    return (
        f"Admin Industri Pertanian Terpadu. Today: {today}. JSON valid only.\n"
        'Format: {"transaksi":[{"produk":"","keterangan":"","kuantitas":0,"satuan":"",'
        '"total_harga":null,"harga_satuan":null,"tanggal":"YYYY-MM-DD","is_penjualan":true,"unit_usaha":""}]}\n'
        "Strict rules:\n"
        "1. Not a transaction -> {\"transaksi\":[]}.\n"
        "2. Buy/beli/pemb -> is_penjualan=false, unit_usaha from: " + units + ".\n"
        "3. Sell/jual/penj -> is_penjualan=true, valid catalog: " + products + ".\n"
        "4. No reasoning, no markdown, just compact JSON."
    )


def _retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def _is_likely_transaction(message: str) -> bool:
    """Guard to filter out non-transaction messages."""
    text = message.strip().lower()
    # Skip short messages, greetings, or common commands
    if len(text) < 5:
        return False
    if not any(c.isdigit() for c in text):
        return False
    
    skip_keywords = {"help", "rekap", "gaji", "absen", "halo", "hai", "menu", "backup", "slip", "pinjam"}
    if any(kw in text for kw in skip_keywords):
        return False
        
    # Check for transaction keywords
    tx_keywords = {"jual", "penj", "beli", "pemb", "laku", "terjual", "kilo", "kg", "butir", "ekor", "unit"}
    if any(kw in text for kw in tx_keywords):
        return True
        
    # Check if it contains pipe format
    if "|" in text:
        return True
        
    return False


def _local_parse_regex(message: str) -> dict | None:
    """Regex-based parser for common transaction formats."""
    transactions = []
    lines = message.splitlines()
    
    # Patterns for: (jual/beli) (produk) (qty) (satuan) (harga) (unit)
    # 1. jual telur 100 butir 1500
    # 2. beli listrik 1 bulan 1200000 unit Pertanian
    # 3. telur 100 butir 1500
    # 4. penj telur fazza | jual ke warung | 100 butir | 1500
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # Action type
        is_sale = True
        action_match = re.match(r"^(jual|penjualan|penj|beli|pembelian|pemb)\s+(.+)$", line, re.I)
        if action_match:
            action, rest = action_match.groups()
            is_sale = action.lower() in {"jual", "penjualan", "penj"}
            line_to_parse = rest
        else:
            line_to_parse = line
            
        # Try pipe format first (existing logic)
        pipe_fields = [f.strip() for f in line_to_parse.split("|")]
        if len(pipe_fields) >= 3:
            # Handle the rest of pipe parsing... but we'll use a more generic regex for simplicity
            pass

        # Generic format: <product> <qty> <unit> <price> [unit <biz_unit>]
        # Example: "telur ayam fazza 100 butir 1500"
        # Example: "listrik 1 bulan 1200000 unit Pertanian"
        pattern = r"^(.*?)\s+([\d.,]+)\s+([a-zA-Z]+)\s+([\d.,\s]*(?:jt|ribu|rb|k|juta)?)(?:\s+unit\s+(.+))?$"
        match = re.search(pattern, line_to_parse, re.I)
        
        if match:
            product, qty, unit, price, biz_unit = match.groups()
            product = product.strip()
            description_match = re.search(r"\b(jual\s+ke\b.*)$", product, re.I)
            description = ""
            if description_match:
                description = description_match.group(1).strip()
                product = product[:description_match.start()].strip()
            
            # Basic validation
            nominal = parse_amount(price)
            if nominal <= 0: continue
            
            transactions.append({
                "produk": product,
                "keterangan": description,
                "kuantitas": qty.replace(".", "").replace(",", "."),
                "satuan": unit.upper(),
                "total_harga": None,
                "harga_satuan": nominal,
                "is_penjualan": is_sale,
                "unit_usaha": biz_unit.strip() if biz_unit else "",
                "tanggal": now_tz().strftime("%Y-%m-%d")
            })
            
    return {"transaksi": transactions} if transactions else None


def _parse_pipe_transactions(message: str) -> dict | None:
    transactions = []
    for line in message.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() in {"rekap minggu ini", "rekap bulan ini", "help"}:
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) not in {4, 5}:
            return None
        action, description, quantity_text, price_text = fields[:4]
        action_match = re.match(r"^(penj|penjualan|jual|pemb|pembelian|beli)\s+(.+)$", action, re.I)
        quantity_match = re.fullmatch(r"([\d.,]+)\s+(.+)", quantity_text)
        price_match = re.fullmatch(r"[\d.,]+", price_text)
        if not action_match or not quantity_match or not price_match:
            return None
        action_type, product = action_match.groups()
        quantity, unit = quantity_match.groups()
        is_sale = action_type.lower() in {"penj", "penjualan", "jual"}
        business_unit = ""
        if len(fields) == 5:
            unit_match = re.fullmatch(r"unit\s+(.+)", fields[4], re.I)
            if not unit_match:
                return None
            business_unit = unit_match.group(1).strip()
        transactions.append({
            "produk": product,
            "keterangan": description,
            "kuantitas": quantity.replace(".", "").replace(",", "."),
            "satuan": unit,
            "total_harga": None,
            "harga_satuan": price_text.replace(".", "").replace(",", ""),
            "is_penjualan": is_sale,
            "unit_usaha": "" if is_sale else business_unit,
        })
    return {"transaksi": transactions} if transactions else None


def analyze_sales(message: str) -> dict:
    # 1. Guard: Check if likely a transaction
    if not _is_likely_transaction(message):
        track_groq("skipped_guard")
        return {"transaksi": []}

    # 2. Local Parse: Try regex/pipe first
    pipe_transactions = _parse_pipe_transactions(message)
    if pipe_transactions:
        track_groq("local_parse_pipe")
        return pipe_transactions
        
    local_transactions = _local_parse_regex(message)
    if local_transactions:
        track_groq("local_parse_regex")
        return local_transactions

    # 3. Cache: Check if we have this message cached
    msg_hash = hashlib.sha256(message.strip().lower().encode()).hexdigest()
    cached = groq_cache.get(msg_hash)
    if cached:
        track_groq("cache_hit")
        return cached

    # 4. API Call: If all else fails, use Groq
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY belum diisi.")
        
    track_groq("api_call")
    today = now_tz().strftime("%Y-%m-%d")
    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _sales_prompt(today)},
            {"role": "user", "content": message},
        ],
        "temperature": 0.1,
        "max_tokens": 512,  # Optimized from 6000
        "response_format": {"type": "json_object"},
    }
    last_error = None
    for attempt in range(2):
        try:
            response = requests.post(
                config.GROQ_URL,
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                json=payload,
                timeout=20,
            )
            if _retryable_status(response.status_code) and attempt == 0:
                last_error = RuntimeError(f"Groq API error {response.status_code}: {response.text[:300]}")
                time.sleep(1.5)
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError(f"Groq API error {response.status_code}: {response.text[:500]}")
            
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            parsed = json.loads(_extract_json(content))
            
            # Normalize structure
            result = {"transaksi": []}
            if isinstance(parsed, dict) and isinstance(parsed.get("transaksi"), list):
                result = parsed
            elif isinstance(parsed, list):
                result = {"transaksi": parsed}
            elif isinstance(parsed, dict) and parsed.get("produk"):
                result = {"transaksi": [parsed]}
            else:
                raise ValueError("Struktur JSON respons Groq tidak sesuai.")

            # Cache the successful result
            groq_cache.set(msg_hash, result)
            return result

        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(1.5)
                continue
            raise RuntimeError(f"Groq tidak dapat dijangkau: {error}") from error
    raise last_error or RuntimeError("Groq gagal setelah retry.")