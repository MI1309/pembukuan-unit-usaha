import unittest
from unittest.mock import patch

from app.repositories import sheet_repo
from app.services import sales_service
from app.services import setup_service
from app.setup_reference_structure import SPECS, _ensure_spec, _looks_like_legacy_sale


class SheetStructureTests(unittest.TestCase):
    def test_recognizes_legacy_sale_with_formatted_rupiah_amount(self):
        row = ["Jual Ayam Jantan Fazza", "INV/20261110112957", "2026-11-10", "penj.", 10, "EKOR", "Rp350.000"]

        self.assertTrue(_looks_like_legacy_sale(row))

    def test_recognizes_legacy_sale_with_locale_formatted_date(self):
        row = ["Jual Ayam Jantan Fazza", "INV/20261110112957", "10/11/2026", "penj.", "10", "EKOR", "350000"]

        self.assertTrue(_looks_like_legacy_sale(row))

    def test_setup_inserts_header_above_legacy_sale_without_overwriting_it(self):
        legacy_row = ["Jual Ayam Jantan Fazza", "INV/20261110112957", "2026-11-10", "penj.", 10, "EKOR", "Rp350.000"]
        worksheet = unittest.mock.Mock()
        worksheet.get.return_value = [legacy_row]
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet.return_value = worksheet
        spec = next(spec for spec in SPECS if spec["name"] == "Rincian Penjualan")

        result = _ensure_spec(spreadsheet, spec, apply=True)

        self.assertEqual(result, "HEADER INSERTED; DATA PRESERVED")
        worksheet.insert_row.assert_called_once_with(spec["headers"], index=1, value_input_option="RAW")

    def test_setup_preserves_legacy_row_with_unrecognized_cell_formats(self):
        legacy_row = ["Jual Ayam Jantan Fazza", "INV/20261110112957", "10 Nov 2026", "penj.", "10 ekor", "EKOR", "Rp 350.000,00"]
        worksheet = unittest.mock.Mock()
        worksheet.get.return_value = [legacy_row]
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet.return_value = worksheet
        spec = next(spec for spec in SPECS if spec["name"] == "Rincian Penjualan")

        result = _ensure_spec(spreadsheet, spec, apply=True)

        self.assertEqual(result, "HEADER INSERTED; DATA PRESERVED")
        worksheet.insert_row.assert_called_once_with(spec["headers"], index=1, value_input_option="RAW")

    def test_reads_daily_detail_headers_at_row_five(self):
        values = [
            ["Industri Pertanian Terpadu Lentera Farm"],
            [],
            ["Periode: November 2026"],
            [],
            ["Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi"],
            ["2026-11-13", "Telur", "Penjualan"],
        ]

        records = sheet_repo._records_from_values(values)

        self.assertEqual(records, [{
            "Tanggal": "2026-11-13",
            "Nama Produk /Jasa/ Barang": "Telur",
            "Keterangan Transaksi": "Penjualan",
        }])

    def test_monthly_recap_uses_reference_monthly_sheet(self):
        with patch.object(sales_service, "_sales_rows", return_value=[]), patch.object(
            sales_service, "_replace_recap_rows"
        ) as replace_rows:
            from datetime import date

            sales_service.save_monthly_recap(date(2026, 11, 1))

        replace_rows.assert_called_once_with("(1)Rekap Bulanan ", "2026-11", [])

    def test_archives_transactions_then_removes_source_rows(self):
        archive = unittest.mock.Mock()
        unit = unittest.mock.Mock()
        unit.row_values.return_value = ["", "2026-10-01", "Pupuk"]
        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name, create=True: {
            "_ArsipTransaksi": archive,
            "(3)Pertanian ": unit,
        }[name]), patch.object(sheet_repo, "invalidate_cache"):
            count = sheet_repo.archive_unit_transactions({
                "(3)Pertanian ": [(4, ["", "2026-10-01", "Pupuk"])],
            }, "2026-11-13 08:00:00")

        self.assertEqual(count, 1)
        archive.append_rows.assert_called_once_with(
            [["(3)Pertanian ", "", "2026-10-01", "Pupuk", "", "", "", "", "", "", "2026-11-13 08:00:00"]],
            value_input_option="USER_ENTERED",
        )
        unit.delete_rows.assert_called_once_with(4)

    def test_unit_setup_sends_formatting_as_one_batch(self):
        worksheet = unittest.mock.Mock()
        worksheet.id = 42
        worksheet.get_all_values.return_value = [
            ["Kakorlap Unit Usaha Pertanian"],
            ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk"],
        ]
        spreadsheet = unittest.mock.Mock()
        worksheet.spreadsheet = spreadsheet
        with patch.object(sheet_repo, "get_sheet", return_value=worksheet):
            setup_service.setup_sheet_unit("(3)Pertanian ")

        spreadsheet.batch_update.assert_called_once()

    def test_employee_and_recap_formatting_uses_one_batch(self):
        spreadsheet = unittest.mock.Mock()
        worksheets = {}
        for sheet_id, name in enumerate([
            "Database Karyawan", "Presensi Karyawan", "Slip Gaji", "Penyesuaian Gaji",
            "_RekapHarian", "(1)Rekap Bulanan ",
        ], start=1):
            worksheet = unittest.mock.Mock()
            worksheet.id = sheet_id
            worksheet.spreadsheet = spreadsheet
            worksheets[name] = worksheet

        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name: worksheets[name]):
            setup_service._format_karyawan_sheets()

        spreadsheet.batch_update.assert_called_once()


if __name__ == "__main__":
    unittest.main()