from app.repositories import sheet_repo
from app.services.periodic_total_service import rebuild_all_sheets_with_totals
from app.config import config

def fix_headers_and_sync():
    print("=== MEROMBAK SELURUH DATA, HEADER, JUDUL & SUBTOTAL ===")
    results = rebuild_all_sheets_with_totals()
    for unit, status in results.items():
        print(f"[{status}] {unit}")
        
    print("\n✅ Selesai! Silakan cek Google Sheets Anda.")

if __name__ == "__main__":
    fix_headers_and_sync()