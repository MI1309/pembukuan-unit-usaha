from app.core.utils import format_rupiah

def build_rekap_bulanan() -> str:
    from app.services.sales_service import _sales_rows

    data = _sales_rows()
    if not data:
        return "Belum ada data."
    total = sum(row["amount"] for row in data)
    return f"📊 *REKAP PENJUALAN*\nTotal: {format_rupiah(total)}"