import unittest
from unittest.mock import patch

from app.repositories import sheet_repo
from app.services import sales_service
from app.services import setup_service
from app.setup_reference_structure import LEDGER_HEADERS, SPECS, _ensure_spec, _looks_like_legacy_sale


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

    def test_skip_subtotals_uses_keterangan_header_column(self):
        values = [
            ["Kakorlap Unit Usaha Pertanian"],
            ["Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
            ["2026-11-01", "Pupuk", "Pembelian", "1", "kg", "0", "100", "-100", "TX1"],
            ["2026-11-07", "", "[SUBTOTAL MINGGU 1]", "", "", "0", "100", "-100", ""],
        ]

        records = sheet_repo._records_from_values(values, skip_subtotals=True)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["Nama Barang/Jasa /Produk"], "Pupuk")

    def test_skip_subtotals_uses_daily_description_header(self):
        values = [
            ["Industri Pertanian Terpadu Lentera Farm"], [], ["Periode: November 2026"], [],
            ["Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Transaction ID"],
            ["2026-11-01", "Telur", "Penjualan", "1", 1500, 1500, 0, "TX1"],
            ["", "", "[TOTAL BULAN November 2026]", "", "", 1500, 0, ""],
        ]

        records = sheet_repo._records_from_values(values, skip_subtotals=True)

        assert len(records) == 1
        assert records[0]["Nama Produk /Jasa/ Barang"] == "Telur"

    def test_rewrite_period_block_refreshes_stale_grid_id_once(self):
        sheet_name = "(3)Pertanian "
        stale = unittest.mock.Mock(id=361071210, title=sheet_name)
        fresh = unittest.mock.Mock(id=987654321, title=sheet_name)
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet.return_value = fresh
        original_error = RuntimeError("APIError: [400]: No grid with id: 361071210")
        spreadsheet.batch_update.side_effect = [original_error, None]
        rows = [["2026-11-01", "Produk", "", "1", "kg", 100, 0, 100, "TX1"]]

        with patch.object(sheet_repo, "get_spreadsheet", return_value=spreadsheet), \
             patch.dict(sheet_repo._worksheet_cache, {sheet_name: stale}), \
             patch.object(sheet_repo, "invalidate_cache"):
            sheet_repo.rewrite_period_block(sheet_name, 3, 3, rows)

        assert spreadsheet.batch_update.call_count == 2
        first = spreadsheet.batch_update.call_args_list[0].args[0]["requests"][0]["updateCells"]["range"]
        second = spreadsheet.batch_update.call_args_list[1].args[0]["requests"][0]["updateCells"]["range"]
        assert first["sheetId"] == stale.id
        assert second["sheetId"] == fresh.id

    def test_rewrite_period_block_reraises_original_stale_grid_error(self):
        sheet_name = "(3)Pertanian "
        stale = unittest.mock.Mock(id=361071210, title=sheet_name)
        fresh = unittest.mock.Mock(id=987654321, title=sheet_name)
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet.return_value = fresh
        original_error = RuntimeError("APIError: [400]: No grid with id: 361071210")
        spreadsheet.batch_update.side_effect = [original_error, RuntimeError("second failure")]

        with patch.object(sheet_repo, "get_spreadsheet", return_value=spreadsheet), \
             patch.dict(sheet_repo._worksheet_cache, {sheet_name: stale}):
            with self.assertRaisesRegex(RuntimeError, "No grid with id: 361071210"):
                sheet_repo.rewrite_period_block(sheet_name, 3, 3, [["row"]])

    def test_get_all_records_many_uses_sheet_ttl(self):
        response = {"Ledger!A:Z": [["Tanggal", "Barang"], ["2026-11-16", "Pakan"]]}
        record_cache = {}
        with patch.object(sheet_repo, "_record_cache", record_cache), \
             patch.object(sheet_repo, "_batch_get_ranges", return_value=response), \
             patch.object(sheet_repo, "monotonic", return_value=100):
            records = sheet_repo.get_all_records_many(["Ledger"])

        self.assertEqual(records["Ledger"], [{"Tanggal": "2026-11-16", "Barang": "Pakan"}])
        self.assertEqual(record_cache["Ledger"][0], 103)

    def test_monthly_recap_uses_reference_monthly_sheet(self):
        with patch.object(sales_service, "_sales_rows", return_value=[]), patch.object(
            sales_service, "_replace_recap_rows"
        ) as replace_rows:
            from datetime import date

            sales_service.save_monthly_recap(date(2026, 11, 1))

        replace_rows.assert_called_once_with("(1)Rekap Bulanan ", "2026-11", [])

    def test_archives_transactions_then_removes_source_rows(self):
        unit_name = "(3)Pertanian "
        source_row = ["", "2026-10-01", "Pupuk"]
        transaction_id = sheet_repo._legacy_row_transaction_id(unit_name, 4, source_row)
        archive_headers = [
            "Unit Usaha", "Nomor", "Tanggal", "Nama Barang/Jasa/Produk", "Keterangan",
            "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Saldo", "Diarsipkan",
        ]
        archived_values = [
            [*archive_headers, "Transaction ID"],
            [unit_name, *source_row, *([""] * 6), "2026-11-13 08:00:00", transaction_id],
        ]
        archive = unittest.mock.Mock()
        archive.get_all_values.side_effect = [
            [archive_headers],
            [[*archive_headers, "Transaction ID"]],
            archived_values,
        ]
        unit = unittest.mock.Mock()
        unit.get_all_values.return_value = [
            ["Kakorlap Unit Usaha Pertanian"],
            ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Transaction ID"],
            [],
            source_row,
        ]
        unit.row_values.return_value = [*source_row, transaction_id]
        metadata = unittest.mock.Mock()
        metadata.get_all_values.return_value = [["Key", "Value"]]
        sheets = {"_ArsipTransaksi": archive, unit_name: unit, "_Meta": metadata}
        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name, create=True: sheets[name]), \
             patch.object(sheet_repo, "invalidate_cache"):
            count = sheet_repo.archive_unit_transactions({
                unit_name: [(4, source_row)],
            }, "2026-11-13 08:00:00")

        self.assertEqual(count, 1)
        archive.append_rows.assert_called_once_with(
            [[unit_name, "", "2026-10-01", "Pupuk", "", "", "", "", "", "", "2026-11-13 08:00:00", transaction_id]],
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

        worksheet.update.assert_called_with('A2:I2', [LEDGER_HEADERS])
        spreadsheet.batch_update.assert_called_once()

    def test_unit_ledger_append_formats_date_amount_and_id_columns(self):
        sheet_name = "(4)Ternak Ayam Fazza"

        self.assertEqual(
            [header.casefold() for header in LEDGER_HEADERS[-2:]],
            ["sisa saldo", "transaction id"],
        )
        self.assertEqual(
            sheet_repo._appended_cell_format(sheet_name, 0)["numberFormat"]["type"],
            "DATE",
        )
        self.assertEqual(
            sheet_repo._appended_cell_format(sheet_name, 4)["numberFormat"]["type"],
            "NUMBER",
        )
        self.assertEqual(
            sheet_repo._appended_cell_format(sheet_name, 7)["numberFormat"]["type"],
            "CURRENCY",
        )
        self.assertNotIn("numberFormat", sheet_repo._appended_cell_format(sheet_name, 8))

    def test_transaction_id_header_repairs_swapped_balance_and_id_labels(self):
        sheet_name = "(4)Ternak Ayam Fazza"
        worksheet = unittest.mock.Mock(title=sheet_name)
        old_headers = [*LEDGER_HEADERS[:7], "Transaction ID", "Sisa Saldo"]
        values = [["Kakorlap Unit Usaha Fazza"], old_headers]

        with patch.object(sheet_repo, "_cached_get_all_values", return_value=values), \
             patch.object(sheet_repo, "invalidate_cache"):
            repaired = sheet_repo._ensure_transaction_id_header(worksheet)

        self.assertTrue(repaired)
        worksheet.update.assert_called_once_with(
            range_name="A2:I2", values=[LEDGER_HEADERS],
        )

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

    def test_column_name_conversion(self):
        self.assertEqual(sheet_repo._column_name(1), "A")
        self.assertEqual(sheet_repo._column_name(26), "Z")
        self.assertEqual(sheet_repo._column_name(27), "AA")
        self.assertEqual(sheet_repo._column_name(28), "AB")
        self.assertEqual(sheet_repo._column_name(702), "ZZ")

    def test_append_rows_atomically_sorts_with_column_name(self):
        worksheet = unittest.mock.Mock()
        worksheet.get_all_values.return_value = [
            ["Header 1", "Header 2", "Header 3"],
            ["1", "2026-11-15", "Item B"],
            ["2", "2026-11-14", "Item A"],
        ]
        spreadsheet = unittest.mock.Mock()
        with patch.object(sheet_repo, "get_spreadsheet", return_value=spreadsheet), \
             patch.object(sheet_repo, "get_sheet", return_value=worksheet), \
             patch.object(sheet_repo, "_safe_append"), \
             patch.object(sheet_repo, "invalidate_cache"):
            sheet_repo.append_rows_atomically([("(4)Ternak Ayam Fazza", ["test"])])

        self.assertEqual(worksheet.update.call_count, 2)
        self.assertEqual(worksheet.update.call_args_list[0].kwargs.get("range_name"), "A1:D1")
        self.assertEqual(worksheet.update.call_args_list[1].kwargs.get("range_name"), "A2:D3")

    def test_daily_detail_append_copies_template_format_and_sorts_by_date(self):
        sheet_name = "(2)Rincian Transaksi Harian"
        worksheet = unittest.mock.Mock(id=17)
        worksheet.get_all_values.return_value = [
            ["Title"], ["Subtitle"], ["Period"], [],
            ["Tanggal", "Nama Produk", "Keterangan", "Kuantitas", "Satuan", "Pemasukan", "Pengeluaran", "Transaction ID"],
            ["2026-11-16", "Item existing", "", "1", "unit", 100, 0, "tx-existing"],
        ]
        spreadsheet = unittest.mock.Mock()
        new_rows = [
            ["2026-11-01", "Item earlier", "", "", "", 100, "", "tx-b"],
            ["2026-11-16", "Item later", "", "", "", 200, "", "tx-a"],
        ]

        with patch.object(sheet_repo, "get_spreadsheet", return_value=spreadsheet), \
             patch.object(sheet_repo, "get_sheet", return_value=worksheet), \
             patch.object(sheet_repo, "_ensure_transaction_id_header"), \
               patch.object(sheet_repo, "_safe_append", side_effect=lambda operation, *_args: operation()), \
             patch.object(sheet_repo, "invalidate_cache"):
            sheet_repo.append_rows_atomically([(sheet_name, row) for row in new_rows])

        requests = spreadsheet.batch_update.call_args.args[0]["requests"]
        copy_request = next(request["copyPaste"] for request in requests if "copyPaste" in request)
        sort_request = next(request["sortRange"] for request in requests if "sortRange" in request)
        self.assertEqual(copy_request["pasteType"], "PASTE_FORMAT")
        self.assertEqual(copy_request["source"]["startRowIndex"], 5)
        self.assertEqual(copy_request["source"]["endColumnIndex"], 8)
        self.assertEqual(copy_request["destination"]["startRowIndex"], 6)
        self.assertEqual(copy_request["destination"]["endColumnIndex"], 8)
        self.assertEqual(sort_request["range"]["startRowIndex"], 5)
        self.assertEqual(sort_request["range"]["endRowIndex"], 8)
        self.assertEqual(sort_request["range"]["endColumnIndex"], 8)
        self.assertEqual(sort_request["sortSpecs"][0]["dimensionIndex"], 0)
        self.assertEqual(sort_request["sortSpecs"][1]["dimensionIndex"], 7)
        id_header_format = next(
            request["repeatCell"] for request in requests
            if request.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 7
        )
        self.assertEqual(id_header_format["range"]["startRowIndex"], 4)
        self.assertEqual(id_header_format["range"]["endColumnIndex"], 8)
        self.assertTrue(id_header_format["cell"]["userEnteredFormat"]["textFormat"]["bold"])

    def test_append_refreshes_stale_worksheet_grid_id_once(self):
        sheet_name = "(4)Ternak Ayam Fazza"
        stale = unittest.mock.Mock(id=361071210, title=sheet_name)
        fresh = unittest.mock.Mock(id=987654321, title=sheet_name)
        stale.get_all_values.return_value = [LEDGER_HEADERS]
        fresh.get_all_values.return_value = [LEDGER_HEADERS]
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet.return_value = fresh
        spreadsheet.batch_update.side_effect = [
            RuntimeError("APIError: [400]: Invalid requests[0].appendCells: No grid with id: 361071210"),
            None,
        ]
        row = ["2026-11-16", "Produk", "", "1", "unit", 100, 0, 100, "20261116-TAF-12345678"]

        with patch.object(sheet_repo, "get_spreadsheet", return_value=spreadsheet), \
             patch.dict(sheet_repo._worksheet_cache, {sheet_name: stale}), \
             patch.object(sheet_repo, "_cached_get_all_values", return_value=[LEDGER_HEADERS]), \
             patch.object(sheet_repo, "_safe_append", side_effect=lambda operation, *_args: operation()), \
             patch.object(sheet_repo, "invalidate_cache"):
            sheet_repo.append_rows_atomically([(sheet_name, row)])

        assert spreadsheet.batch_update.call_count == 2
        first_requests = spreadsheet.batch_update.call_args_list[0].args[0]["requests"]
        second_requests = spreadsheet.batch_update.call_args_list[1].args[0]["requests"]
        assert first_requests[0]["appendCells"]["sheetId"] == stale.id
        assert second_requests[0]["appendCells"]["sheetId"] == fresh.id

    def test_append_reraises_original_error_when_refreshed_grid_retry_fails(self):
        sheet_name = "(4)Ternak Ayam Fazza"
        stale = unittest.mock.Mock(id=361071210, title=sheet_name)
        fresh = unittest.mock.Mock(id=987654321, title=sheet_name)
        stale.get_all_values.return_value = [LEDGER_HEADERS]
        fresh.get_all_values.return_value = [LEDGER_HEADERS]
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet.return_value = fresh
        original_error = RuntimeError("APIError: [400]: No grid with id: 361071210")
        spreadsheet.batch_update.side_effect = [original_error, RuntimeError("retry failed")]
        row = ["2026-11-16", "Produk", "", "1", "unit", 100, 0, 100, "20261116-TAF-12345678"]

        with patch.object(sheet_repo, "get_spreadsheet", return_value=spreadsheet), \
             patch.dict(sheet_repo._worksheet_cache, {sheet_name: stale}), \
             patch.object(sheet_repo, "_cached_get_all_values", return_value=[LEDGER_HEADERS]), \
             patch.object(sheet_repo, "_safe_append", side_effect=lambda operation, *_args: operation()), \
             patch.object(sheet_repo, "invalidate_cache"):
            with self.assertRaisesRegex(RuntimeError, "No grid with id: 361071210"):
                sheet_repo.append_rows_atomically([(sheet_name, row)])

    def test_find_subtotal_rows_uses_keterangan_header_column(self):
        sheet_name = "(4)Ternak Ayam Fazza"
        worksheet = unittest.mock.Mock()
        values = [
            ["Title"],
            ["Tanggal", "Produk", "Keterangan Transaksi", "Kuantitas", "Transaction ID"],
            ["2026-11-16", "Telur", "jual ke toko", "10 butir", "tx-1"],
            ["2026-11-21", "", "SUBTOTAL MINGGU 3 (15-21 November 2026)", "", ""],
        ]
        with patch.object(sheet_repo, "get_sheet", return_value=worksheet), \
             patch.object(sheet_repo, "_cached_get_all_values", return_value=values):
            rows = sheet_repo.find_subtotal_rows(sheet_name, "SUBTOTAL MINGGU 3")

        self.assertEqual(rows, [4])


if __name__ == "__main__":
    unittest.main()