from unittest.mock import Mock, patch

from app import migrate_unit_ledger_layout
from app.setup_reference_structure import LEDGER_HEADERS


class FakeLedger:
    def __init__(self, values):
        self.values = values
        self.id = 42
        self.spreadsheet = Mock()
        self.batch_updates = []

    def get_all_values(self):
        return self.values

    def batch_update(self, updates, **kwargs):
        self.batch_updates.append((updates, kwargs))


def _ledger_values():
    return [
        ["Kakorlap Unit Usaha Fazza"],
        [
            "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas",
            "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Transaction ID", "Sisa Saldo",
        ],
        ["16 Nov 2026", "Telur", "Warung A", "100 BUTIR", "150000", "15000000", "", "15000000", "20261116-TAF-0123abcd"],
        ["16 Nov 2026", "Telur", "Warung B", "80 BUTIR", "120000", "9600000", "", "20261116-TAF-4567abcd", "9600000"],
    ]


def test_layout_plan_detects_header_mismatch_and_only_swaps_misordered_ids():
    plan = migrate_unit_ledger_layout._layout_plan("(4)Ternak Ayam Fazza", _ledger_values())

    assert plan["status"] == "ready"
    assert plan["header_changed"] is True
    assert plan["swaps"] == [(4, "9600000", "20261116-TAF-4567abcd")]
    assert plan["current_header"] == _ledger_values()[1]


def test_migration_dry_run_does_not_write():
    ledger = FakeLedger(_ledger_values())
    with patch.object(migrate_unit_ledger_layout.config, "UNIT_USAHA", ["(4)Ternak Ayam Fazza"]), \
         patch.object(migrate_unit_ledger_layout.sheet_repo, "get_sheet", return_value=ledger):
        results = migrate_unit_ledger_layout.migrate_unit_ledgers(apply=False)

    assert results["(4)Ternak Ayam Fazza"]["status"] == "ready"
    assert ledger.batch_updates == []
    ledger.spreadsheet.batch_update.assert_not_called()


def test_apply_migration_standardizes_header_swaps_only_bad_rows_and_formats_sheet():
    ledger = FakeLedger(_ledger_values())
    with patch.object(migrate_unit_ledger_layout.config, "UNIT_USAHA", ["(4)Ternak Ayam Fazza"]), \
         patch.object(migrate_unit_ledger_layout.sheet_repo, "get_sheet", return_value=ledger), \
         patch.object(migrate_unit_ledger_layout.sheet_repo, "invalidate_cache"):
        results = migrate_unit_ledger_layout.migrate_unit_ledgers(apply=True)

    updates, kwargs = ledger.batch_updates[0]
    assert updates[0] == {"range": "A2:I2", "values": [LEDGER_HEADERS]}
    assert updates[1] == {
        "range": "H4:I4",
        "values": [["9600000", "20261116-TAF-4567abcd"]],
    }
    assert kwargs["value_input_option"] == "USER_ENTERED"
    ledger.spreadsheet.batch_update.assert_called_once()
    assert results["(4)Ternak Ayam Fazza"]["status"] == "updated"


def test_daily_detail_migration_formats_existing_expense_values_as_rupiah():
    daily = FakeLedger([
        ["Industri Pertanian Terpadu Lentera Farm"],
        [],
        [],
        [],
        [
            "Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas",
            "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Transaction ID",
        ],
        ["16 Nov 2026", "Listrik", "Tagihan November", "1 bulan", "1100000", "", "1100000", "20261116-TAF-0123abcd"],
    ])
    with patch.object(migrate_unit_ledger_layout.config, "UNIT_USAHA", []), \
         patch.object(migrate_unit_ledger_layout.sheet_repo, "get_sheet", return_value=daily), \
         patch.object(migrate_unit_ledger_layout.sheet_repo, "invalidate_cache"):
        results = migrate_unit_ledger_layout.migrate_unit_ledgers(apply=True)

    requests = daily.spreadsheet.batch_update.call_args.args[0]["requests"]
    expense_format = next(
        request["repeatCell"] for request in requests
        if request.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 4
    )
    assert expense_format["range"]["endColumnIndex"] == 7
    assert expense_format["cell"]["userEnteredFormat"]["numberFormat"] == {
        "type": "CURRENCY", "pattern": '"Rp" #,##0',
    }
    assert results["(2)Rincian Transaksi Harian"]["status"] == "updated"
    daily_updates, _ = daily.batch_updates[0]
    assert daily_updates[0]["range"] == "A5:H6"
    assert daily_updates[0]["values"][0][-1] == "Transaction ID"
    assert daily_updates[0]["values"][1][7] == "20261116-TAF-0123abcd"
