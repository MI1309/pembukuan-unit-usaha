from datetime import datetime
from app.core.groq_ai import analyze_sales
from app.core.utils import parse_amount, format_rupiah, format_tanggal_id, now_tz
from app.repositories import sheet_repo
from app.config import config

# Pemetaan Produk ke Unit Usaha
def get_unit_sheet(produk_nama):
    produk_nama = produk_nama.lower()
    if "lele" in produk_nama or "ikan" in produk_nama: return "Perikanan Emran"
    if "ayam" in produk_nama: return "Ternak Ayam Fazza" # Asumsi default Fazza
    if "anggur" in produk_nama: return "Pertanian Anggur"
    if "telur" in produk_nama: return "Ternak Ayam Fazza"
    return "Pertanian Jagung" # Default fallback

def process_sales(message: str):
    parsed = analyze_sales(message)
    items = parsed.get("transaksi", [])
    saved, gagal, total_semua = [], [], 0

    for raw in items:
        produk_kotor = str(raw.get("produk", "")).lower()
        resolved = None
        for k in config.KATALOG:
            if any(ku in produk_kotor for ku in k["kunci"]):
                resolved = k
                break
                
        if not resolved:
            gagal.append(f"{raw.get('produk')} — produk tidak dikenal")
            continue
            
        kuantitas = float(raw.get("kuantitas") or 0)
        penjualan = parse_amount(raw.get("total_harga"))
        
        if kuantitas <= 0 or penjualan <= 0:
            gagal.append(f"{resolved['nama']} — jumlah/harga salah")
            continue
            
        # Tentukan Unit Usaha
        target_sheet = get_unit_sheet(resolved['nama'])
        
        # Tulis ke Sheet Unit (Pemasukan)
        # Format: [Nomor, Tanggal, Produk, Keterangan, Qty, Satuan, Pemasukan, Pengeluaran, Saldo]
        # Nomor otomatis diurus Google Sheets (atau kita isi '-')
        row = ["-", now_tz().strftime("%Y-%m-%d"), resolved["nama"], raw.get("keterangan", ""), kuantitas, resolved["satuan"], penjualan, 0, penjualan]
        sheet_repo.append_row(target_sheet, row)
        
        saved.append({"produk": resolved["nama"], "total": penjualan, "unit": target_sheet})
        total_semua += penjualan

    if not saved and not gagal: return False, ""
    
    reply = ""
    if saved:
        reply += f"✅ *{len(saved)} TRANSAKSI DISIMPAN*\n━━━━━━━━━━━━\n"
        for s in saved: reply += f"- {s['produk']} ({s['unit']}): {format_rupiah(s['total'])}\n"
        reply += f"━━━━━━━━━━━━\n💰 *Total: {format_rupiah(total_semua)}*"
    if gagal:
        reply += f"\n\n⚠️ *Gagal:*\n" + "\n".join(gagal)
        
    return True, reply

def process_rekap(message: str):
    # Fungsi rekap untuk sementara kita arahkan ke arsip/semua unit
    msg = message.lower()
    if "rekap" not in msg: return None
    
    now = now_tz()
    total_bulan_ini = 0
    
    # Looping semua unit untuk hitung total bulan ini
    for unit in config.UNIT_USAHA:
        data = sheet_repo.get_all_records(unit)
        for row in data:
            try:
                tgl = datetime.strptime(row.get('Tanggal', ''), "%Y-%m-%d")
                if tgl.month == now.month and tgl.year == now.year:
                    total_bulan_ini += parse_amount(row.get('Pemasukan (Rp)', 0))
            except: pass

    return f"📊 *REKAP PENJUALAN (SEMUA UNIT)*\n📅 {format_tanggal_id(now)}\n━━━━━━━━━━━━\n💰 *Total Pemasukan: {format_rupiah(total_bulan_ini)}*"