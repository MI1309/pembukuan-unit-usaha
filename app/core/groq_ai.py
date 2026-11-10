import requests
import json
from app.config import config
from app.core.utils import now_tz

def analyze_sales(message: str) -> dict:
    today = now_tz().strftime("%Y-%m-%d")
    
    # PROMPT DIPERPENDEK (Hanya instruksi esensial)
    prompt = f"""Ekstrak pesan jadi JSON: {{"transaksi":[{{"produk":"","keterangan":"","kuantitas":0,"satuan":"","total_harga":0,"tanggal":"{today}"}}]}}. 
Aturan: Angka polos (rb=x1000, jt=x1000000). Jika bukan jualan, balas {{"transaksi":[]}}."""

    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": message}
        ],
        "temperature": 0.1,
        "max_tokens": 500, # <--- DIBATASI AGAR TIDAK MELEBIHI KUOTA
        "response_format": {"type": "json_object"}
    }
    
    try:
        res = requests.post(config.GROQ_URL, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, json=payload, timeout=15)
        
        # --- HAPUS 2 BARIS PRINT INI JIKA SUDAH BERHASIL ---
        print("STATUS CODE:", res.status_code)
        print("RESPON GROQ:", res.text) 
        # ---------------------------------------------------

        content = res.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"Groq Error: {e}")
        return {"transaksi": []}