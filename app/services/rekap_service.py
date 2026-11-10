from app.repositories import sheet_repo
from app.core.utils import parse_amount, format_rupiah

def build_rekap_bulanan() -> str:
    data = sheet_repo.get_all_values("Rincian Penjualan")
    if not data: return "Belum ada data."
    
    total = 0
    for row in data[1:]: # Skip header
        if len(row) > 6 and row[6].replace('.', '').isdigit():
            total += int(row[6].replace('.', ''))
            
    return f"📊 *REKAP PENJUALAN*\nTotal: {format_rupiah(total)}"