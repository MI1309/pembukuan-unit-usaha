import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.repositories import sheet_repo


class FakeWorksheet:
    def __init__(self, values=None):
        self.values = deepcopy(values or [])
        self.append_behavior = None

    def get_all_values(self):
        return deepcopy(self.values)

    def update_cell(self, row, column, value):
        while len(self.values) < row:
            self.values.append([])
        while len(self.values[row - 1]) < column:
            self.values[row - 1].append("")
        self.values[row - 1][column - 1] = value

    def append_rows(self, rows, **kwargs):
        if self.append_behavior:
            return self.append_behavior(rows)
        self.values.extend(deepcopy(rows))

    def append_row(self, row, **kwargs):
        self.values.append(deepcopy(row))

    def row_values(self, row):
        return deepcopy(self.values[row - 1]) if row <= len(self.values) else []

    def delete_rows(self, row):
        self.values.pop(row - 1)

    def update(self, range_name, values, **kwargs):
        start = range_name.split(":", 1)[0]
        letters = "".join(character for character in start if character.isalpha())
        digits = "".join(character for character in start if character.isdigit())
        row = int(digits or 1)
        column = 0
        for character in letters.upper():
            column = column * 26 + ord(character) - 64
        for row_offset, values_row in enumerate(values):
            for column_offset, value in enumerate(values_row):
                self.update_cell(row + row_offset, column + column_offset, value)


class ArchiveVerificationTests(unittest.TestCase):
    archived_at = "2026-11-15 01:00:00"
    unit_name = "(3)Pertanian "
    headers = [
        "Unit Usaha", "Nomor", "Tanggal", "Nama Barang/Jasa/Produk", "Keterangan",
        "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Saldo",
        "Diarsipkan", "Transaction ID",
    ]
    ledger_headers = [
        "Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi",
        "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID",
    ]

    def setUp(self):
        self.transaction_id = "20261001-PRT-0123abcd"
        self.ledger_row = [
            "INV-1", "2026-10-01", "Pupuk", "Stok", "3 KG", 10000,
            "", 30000, -30000, self.transaction_id,
        ]
        self.archive = FakeWorksheet([self.headers])
        self.ledger = FakeWorksheet([
            ["Judul"], self.ledger_headers, self.ledger_row,
        ])
        self.meta = FakeWorksheet([["Key", "Value"]])
        self.sheets = {
            "_ArsipTransaksi": self.archive,
            self.unit_name: self.ledger,
            "_Meta": self.meta,
        }

    def _archive(self):
        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name, create=True: self.sheets.get(name)):
            return sheet_repo.archive_unit_transactions(
                {self.unit_name: [(3, self.ledger_row)]}, self.archived_at
            )

    def test_verified_copy_writes_manifest_before_deleting_source(self):
        count = self._archive()

        self.assertEqual(count, 1)
        self.assertEqual(len(self.ledger.values), 2)
        self.assertEqual(self.archive.values[1][-1], self.transaction_id)
        manifest_row = next(row for row in self.meta.values if row[0] == f"archive_manifest:{self.archived_at}")
        manifest = json.loads(manifest_row[1])
        self.assertEqual(manifest[self.unit_name]["count"], 1)
        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name, create=True: self.sheets.get(name)):
            verified = sheet_repo.verify_archive_manifest(f"archive_manifest:{self.archived_at}")
        self.assertTrue(verified[self.unit_name]["valid"])

    def test_verification_mismatch_does_not_delete_source_and_logs_failure(self):
        self.archive.append_behavior = lambda rows: self.archive.values.extend([
            [*rows[0][:-1], "20261001-PRT-deadbeef"],
        ])
        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name, create=True: self.sheets.get(name)), \
             patch("app.services.webhook_state.log_event") as log_event:
            with self.assertRaises(sheet_repo.ArchiveVerificationError):
                sheet_repo.archive_unit_transactions(
                    {self.unit_name: [(3, self.ledger_row)]}, self.archived_at
                )

        self.assertEqual(len(self.ledger.values), 3)
        log_event.assert_called_once()
        self.assertEqual(log_event.call_args.args[2], "ARCHIVE_VERIFY_FAILED")

    def test_partial_append_error_does_not_delete_source(self):
        second_row = [
            "INV-2", "2026-10-02", "Pupuk", "Stok tambahan", "4 KG", 10000,
            "", 40000, -40000, "20261002-PRT-4567abcd",
        ]
        self.ledger.values.append(second_row)

        def append_then_raise(rows):
            self.archive.values.extend(deepcopy(rows[:1]))
            raise RuntimeError("simulated append failure")

        self.archive.append_behavior = append_then_raise
        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name, create=True: self.sheets.get(name)), \
             patch("app.services.webhook_state.log_event"):
            with self.assertRaises(sheet_repo.ArchiveVerificationError):
                sheet_repo.archive_unit_transactions(
                    {self.unit_name: [(3, self.ledger_row), (4, second_row)]}, self.archived_at
                )

        self.assertEqual(len(self.ledger.values), 4)

    def test_restore_cli_defaults_to_dry_run(self):
        with patch.object(sheet_repo, "rollback_archive", return_value={self.unit_name: 1}) as restore, \
             patch("builtins.print"):
            from app import verify_archive
            exit_code = verify_archive.main(["--restore", f"archive_manifest:{self.archived_at}"])

        self.assertEqual(exit_code, 0)
        restore.assert_called_once_with(f"archive_manifest:{self.archived_at}", apply=False)

    def test_rollback_restores_missing_ledger_rows(self):
        self._archive()
        self.ledger.values = [["Judul"], self.ledger_headers]

        def append_restored(rows_by_sheet):
            for sheet_name, row in rows_by_sheet:
                self.sheets[sheet_name].values.append(deepcopy(row))

        with patch.object(sheet_repo, "get_sheet", side_effect=lambda name, create=True: self.sheets.get(name)), \
             patch.object(sheet_repo, "append_rows_atomically", side_effect=append_restored):
            restored = sheet_repo.rollback_archive(f"archive_manifest:{self.archived_at}")

        self.assertEqual(restored[self.unit_name], 1)
        self.assertEqual(self.ledger.values[-1], self.ledger_row)


if __name__ == "__main__":
    unittest.main()