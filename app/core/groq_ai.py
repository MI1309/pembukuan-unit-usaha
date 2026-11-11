import json
import re
import time

import requests

from app.config import config
from app.core.utils import now_tz


def _extract_json(content: str) -> str:
    raw = re.sub(r"<think>[\s\S]*?</think>", "", str(content or ""), flags=re.I).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON tidak ditemukan pada respons Groq.")
    return raw[start:end + 1]


def _sales_prompt(today: str) -> str:
    products = "; ".join(
        f"{item['nama']} (kata kunci: {', '.join(item['kunci'])}; satuan: {item['satuan']})"
        for item in config.KATALOG
    )
    units = "; ".join(config.UNIT_USAHA)
    return (
        "Kamu adalah asisten admin pencatatan transaksi Industri Pertanian Terpadu Lentera Farm.\n"
        f"Tanggal hari ini: {today}.\n"
        "Ekstrak SEMUA transaksi penjualan atau pembelian dalam pesan, satu objek per barang/baris, menjadi JSON valid tanpa markdown.\n"
        'Format: {"transaksi":[{"produk":"","keterangan":"","kuantitas":0,"satuan":"",'
        '"total_harga":null,"harga_satuan":null,"tanggal":"YYYY-MM-DD","is_penjualan":true,"unit_usaha":""}]}\n'
        "Aturan ketat:\n"
        "1. Jika bukan perintah mencatat penjualan/pembelian (rekap, salam, pertanyaan), keluarkan {\"transaksi\":[]} .\n"
        "2. Jangan lewatkan atau gabungkan transaksi berbeda.\n"
        "3. Kata pembelian, beli, atau pemb sebagai penanda pengeluaran; penj atau jual sebagai penanda pemasukan.\n"
        f"4. Untuk penjualan, katalog valid hanya: {products}. Produk lain tetap keluarkan agar ditolak aplikasi.\n"
        "5. Pembelian boleh berupa barang/jasa apa pun, termasuk Listrik, UHK, Penyusutan, Konsumsi Dapur, dan PDAM. "
        f"Set is_penjualan=false dan isi unit_usaha dengan nama tab persis dari daftar ini: {units}.\n"
        "6. Setiap transaksi wajib berisi produk/jasa, keterangan transaksi, kuantitas, satuan, dan total atau harga satuan. "
        "Jangan mengarang kuantitas atau satuan; jika tidak disebut isi kuantitas 0 dan satuan kosong agar aplikasi meminta koreksi.\n"
        "7. Jangan menebak unit pembelian. Jika pesan tidak menyebut unit dengan jelas, isi unit_usaha kosong.\n"
        "8. kuantitas adalah jumlah barang/jasa, bukan harga. Satuan huruf besar sesuai pesan/katalog.\n"
        "9. Angka keluaran berupa number polos. Titik ribuan diabaikan; koma desimal. Contoh 1.400.000=1400000, "
        "2,5 jt=2500000, 2,3 ribu=2300. rb/ribu/k dikali 1000; jt/juta dikali 1000000.\n"
        "10. Isi total_harga hanya jika disebut sebagai jumlah total. Isi harga_satuan hanya jika harga per item/kg/ekor/@ disebut. "
        "Jangan mengisi keduanya dan jangan menghitung perkalian sendiri.\n"
        "11. Keterangan transaksi harus mengikuti pesan; tanggal gunakan YYYY-MM-DD dan default hari ini.\n"
        "12. Jangan berikan reasoning, tag thinking, atau teks di luar JSON."
    )


def _retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def analyze_sales(message: str) -> dict:
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY belum diisi.")
    today = now_tz().strftime("%Y-%m-%d")
    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _sales_prompt(today)},
            {"role": "user", "content": message},
        ],
        "temperature": 0.1,
        "max_tokens": 900,
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
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(_extract_json(content))
            if isinstance(parsed, dict) and isinstance(parsed.get("transaksi"), list):
                return parsed
            if isinstance(parsed, list):
                return {"transaksi": parsed}
            if isinstance(parsed, dict) and parsed.get("produk"):
                return {"transaksi": [parsed]}
            raise ValueError("Struktur JSON respons Groq tidak sesuai.")
        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(1.5)
                continue
            raise RuntimeError(f"Groq tidak dapat dijangkau: {error}") from error
    raise last_error or RuntimeError("Groq gagal setelah retry.")