import unittest
from datetime import date

from app.services.sales_service import _unit_rows_before_month


class UnitRolloverTests(unittest.TestCase):
    def test_selects_only_rows_from_months_before_target(self):
        rows = [
            ["Kakorlap Unit Usaha Pertanian"],
            ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk"],
            ["", "2026-10-31", "Transaksi lama"],
            ["", "2026-11-01", "Bulan target"],
            ["", "2026-12-01", "Transaksi masa depan"],
            ["", "", "Baris tanpa tanggal"],
        ]

        result = _unit_rows_before_month(rows, date(2026, 11, 1))

        self.assertEqual(result, [(3, rows[2])])

    def test_does_not_roll_over_when_all_rows_are_in_target_or_future(self):
        rows = [
            ["Judul"],
            ["Nomor", "Tanggal"],
            ["", "2026-11-30"],
            ["", "2026-12-01"],
        ]

        self.assertEqual(_unit_rows_before_month(rows, date(2026, 11, 1)), [])


if __name__ == "__main__":
    unittest.main()