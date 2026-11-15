import unittest
from datetime import date
from unittest.mock import Mock, call, patch

from app.config import config
from app.repositories import sheet_repo
from app.services import report_service


class ReportServiceTests(unittest.TestCase):
    def test_weekly_recap_uses_four_fixed_date_ranges(self):
        summaries = {
            "total_pemasukan_unit": 1000,
            "total_pengeluaran_unit": 200,
            "laba_rugi_unit": 800,
            "total_penjualan_rincian": 500,
        }
        with patch.object(
            report_service,
            "simpan_laporan_ke_sheet",
            side_effect=lambda period, *_: {"periode": period, **summaries},
        ) as save:
            response = report_service.jalankan_rekap_mingguan("2026-11")

        self.assertIn("Minggu 4 - Nov 2026", response)
        self.assertEqual(save.call_args_list, [
            call("Minggu 1 - Nov 2026", "2026-11-01", "2026-11-07"),
            call("Minggu 2 - Nov 2026", "2026-11-08", "2026-11-14"),
            call("Minggu 3 - Nov 2026", "2026-11-15", "2026-11-21"),
            call("Minggu 4 - Nov 2026", "2026-11-22", "2026-11-30"),
        ])

    def test_weekly_recap_uses_leap_day_for_february(self):
        summaries = {
            "total_pemasukan_unit": 0,
            "total_pengeluaran_unit": 0,
            "laba_rugi_unit": 0,
            "total_penjualan_rincian": 0,
        }
        with patch.object(
            report_service,
            "simpan_laporan_ke_sheet",
            side_effect=lambda period, *_: {"periode": period, **summaries},
        ) as save:
            report_service.jalankan_rekap_mingguan("2024-02")

        self.assertEqual(save.call_args_list[-1], call(
            "Minggu 4 - Feb 2024", "2024-02-22", "2024-02-29"
        ))

    def test_generate_laporan_periode_aggregates_unit_income_and_expenses(self):
        worksheet = Mock()
        worksheet.get_all_values.return_value = [
            ["Judul"],
            ["Nomor", "Tanggal", "Produk", "Keterangan", "Qty", "Satuan", "Pemasukan", "Pengeluaran"],
            ["1", "2026-11-02", "Produk A", "", "2", "KG", "1.000", "200"],
            ["2", "2026-12-01", "Produk B", "", "4", "KG", "500", "0"],
        ]
        with patch.object(sheet_repo, "get_sheet", return_value=worksheet):
            result = report_service.generate_laporan_periode(
                "Unit A", "2026-11-01", "2026-11-30"
            )

        self.assertEqual(result, {
            "unit_usa": "Unit A",
            "total_pemasukan": 1000,
            "total_pengeluaran": 200,
            "laba_rugi": 800,
        })

    def test_unit_recap_does_not_add_quantities_with_different_units(self):
        rows = [
            ["1", "2026-11-02", "Produk A", "", "2", "KG", "1000", "0", ""],
            ["2", "2026-11-03", "Produk B", "", "4", "EKOR", "2000", "0", ""],
        ]
        with patch.object(report_service, "_unit_sheet_rows", return_value=rows):
            result = report_service._unit_recap_rows(
                "Unit A", date(2026, 11, 1), date(2026, 11, 30), "November 2026"
            )

        income_rows = [row for row in result if row[1].endswith("Pemasukan")]
        self.assertEqual([(row[2], row[3], row[4], row[5]) for row in income_rows], [
            (4.0, "EKOR", 2000, 1),
            (2.0, "KG", 1000, 1),
        ])

    def test_simpan_laporan_deletes_existing_period_rows_before_append(self):
        unit_sheet = Mock()
        unit_sheet.get_all_values.return_value = [
            ["Judul"],
            ["Nomor", "Tanggal", "Produk", "Keterangan", "Qty", "Satuan", "Pemasukan", "Pengeluaran"],
            ["1", "2026-11-02", "Produk A", "", "2", "KG", "1.000", "200"],
        ]
        sales_sheet = Mock()
        sales_sheet.get_all_values.return_value = [
            ["Produk", "Nomor", "Tanggal", "Keterangan", "Kuantitas", "Satuan", "Total (Rp)"],
            ["Produk B", "INV/1", "2026-11-02", "", "3", "KG", "1.500"],
        ]
        recap_sheet = Mock()
        recap_sheet.title = "_RekapBulanan"
        recap_sheet.get_all_values.return_value = [
            ["Bulan", "Produk/Jasa/Barang", "Kuantitas", "Satuan", "Total (Rp)", "Jumlah Transaksi"],
            ["Minggu 1 - Nov 2026", "Baris lama", "", "", "", ""],
            ["Minggu 2 - Nov 2026", "Tetap", "", "", "", ""],
            ["Minggu 1 - Nov 2026", "Duplikat lama", "", "", "", ""],
        ]
        sheets = {
            "Unit A": unit_sheet,
            "Rincian Penjualan": sales_sheet,
            "_RekapBulanan": recap_sheet,
        }

        with patch.object(config, "UNIT_USAHA", ["Unit A"]), patch.object(
            sheet_repo, "get_sheet", side_effect=lambda name, create=False: sheets.get(name)
        ), patch.object(sheet_repo, "invalidate_cache"):
            result = report_service.simpan_laporan_ke_sheet(
                "Minggu 1 - Nov 2026", "2026-11-01", "2026-11-07"
            )

        self.assertEqual(recap_sheet.delete_rows.call_args_list, [call(4), call(2)])
        appended = recap_sheet.append_rows.call_args.args[0]
        self.assertEqual(len(appended), 3)
        self.assertEqual(result["total_pemasukan_unit"], 1000)
        self.assertEqual(result["total_pengeluaran_unit"], 200)
        self.assertEqual(result["total_penjualan_rincian"], 1500)

    def test_invalid_month_is_rejected(self):
        with self.assertRaises(ValueError):
            report_service.jalankan_rekap_bulanan("2026-13")


if __name__ == "__main__":
    unittest.main()