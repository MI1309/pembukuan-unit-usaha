from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app import normalize_data


@pytest.mark.parametrize(("value", "expected"), [
    ("16 Nov 2026", "2026-11-16"),
    ("16 November 2026", "2026-11-16"),
    ("20261116", "2026-11-16"),
    ("16/11/26", "2026-11-16"),
    (datetime(2026, 11, 16, 8, 30), "2026-11-16"),
])
def test_parse_flexible_date(value, expected):
    assert normalize_data.parse_flexible_date(value) == expected


@pytest.mark.parametrize(("value", "expected"), [
    ("Rp 10.000,00", 10000),
    ("Rp1.234.500", 1234500),
    ("1,234.50", 1235),
    ("-Rp 2.500", -2500),
    ("", 0),
])
def test_parse_money(value, expected):
    assert normalize_data._parse_money(value) == expected


def test_clean_row_data_moves_legacy_id_after_balance_and_normalizes_values():
    row = ["16 Nov 2026", " Telur ", " Warung A ", " 10 ", " BUTIR ", "Rp 10.000,00", "-", "20261116-TAF-0123abcd", "Rp 20.000"]

    cleaned = normalize_data.clean_row_data(row, "TAF")

    assert cleaned == [
        "2026-11-16", "Telur", "Warung A", "10", "BUTIR", 10000, 0,
        20000, "20261116-TAF-0123abcd",
    ]


def test_clean_row_data_recovers_one_column_shift_after_date():
    shifted = [
        "16 Nov 2026", "", " Telur ", " Warung A ", "10", "BUTIR",
        "Rp 10.000", "-", "Rp 20.000", "20261116-TAF-0123abcd",
    ]

    cleaned = normalize_data.clean_row_data(shifted, "TAF")

    assert cleaned == [
        "2026-11-16", "Telur", "Warung A", "10", "BUTIR", 10000, 0,
        20000, "20261116-TAF-0123abcd",
    ]


def test_legacy_transaction_id_is_deterministic_and_uses_unit_code():
    row = ["2026-11-16", " Telur ", "Warung A", "10", "BUTIR", "10000", "0"]

    first = normalize_data.generate_legacy_tx_id(row, "TAF")
    second = normalize_data.generate_legacy_tx_id(row, "TAF")

    assert first == second
    assert first.startswith("20261116-TAF-")
    assert normalize_data.clean_row_data(row, "TAF")[8] == first


def test_prepare_sheet_drops_empty_and_legacy_total_rows_and_sorts():
    worksheet = Mock()
    worksheet.get_all_values.return_value = [
        ["Kakorlap Unit Usaha Fazza"],
        normalize_data.LEDGER_HEADERS,
        ["2026-11-16", "B", "", "1", "kg", "Rp 2.000", "", "", ""],
        [],
        ["", "", "SUBTOTAL MINGGU 3 (15-21 November 2026)", "", "", "", "", "", ""],
        ["", "", "[SUBTOTAL lama]", "", "", "", "", "", ""],
        ["16 Nov 2026", "A", "", "1", "kg", "Rp 1.000", "", "", ""],
    ]
    with patch.object(normalize_data.sheet_repo, "get_sheet", return_value=worksheet), \
         patch.object(normalize_data.sheet_repo, "_retry_google_call", side_effect=lambda operation, *args, **kwargs: operation(*args, **kwargs)):
        plan = normalize_data._prepare_sheet("(4)Ternak Ayam Fazza")

    assert plan["status"] == "ready"
    assert plan["removed_blank"] == 1
    assert plan["removed_totals"] == 2
    assert [row[1] for row in plan["rows"]] == ["A", "B"]


def test_daily_sheet_keeps_seven_fields_and_appends_transaction_id_in_h():
    worksheet = Mock()
    worksheet.get_all_values.return_value = [
        ["Industri Pertanian Terpadu Lentera Farm"], [], ["Periode: November 2026"], [],
        ["Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)"],
        ["16 Nov 2026", " Telur Ayam Emran ", "Jual ke P3", "443 butir", "Rp 1.500", "Rp 664.500", ""],
    ]
    with patch.object(normalize_data.sheet_repo, "get_sheet", return_value=worksheet), \
         patch.object(normalize_data.sheet_repo, "_retry_google_call", side_effect=lambda operation, *args, **kwargs: operation(*args, **kwargs)):
        plan = normalize_data._prepare_sheet(normalize_data.DAILY_SHEET)

    assert plan["status"] == "ready"
    assert plan["column_count"] == 8
    assert len(plan["headers"]) == len(plan["rows"][0]) == 8
    assert plan["headers"][-1] == "Transaction ID"
    assert plan["rows"][0][:7] == ["2026-11-16", "Telur Ayam Emran", "Jual ke P3", "443 butir", 1500, 664500, 0]
    assert plan["rows"][0][7].startswith("20261116-RTH-")


def test_normalize_sheet_dry_run_does_not_write():
    worksheet = Mock()
    worksheet.get_all_values.return_value = [
        normalize_data.LEDGER_HEADERS,
        ["2026-11-16", "A", "", "1", "kg", "100", "0", "100", ""],
    ]
    with patch.object(normalize_data.sheet_repo, "get_sheet", return_value=worksheet), \
         patch.object(normalize_data.sheet_repo, "_retry_google_call", side_effect=lambda operation, *args, **kwargs: operation(*args, **kwargs)):
        result = normalize_data.normalize_sheet("(3)Pertanian ", apply=False)

    assert result["status"] == "ready"
    worksheet.spreadsheet.batch_update.assert_not_called()


def test_apply_rewrites_sheet_with_one_atomic_batch_and_rebuilds_totals():
    worksheet = Mock()
    worksheet.id = 123
    worksheet.get_all_values.return_value = [
        ["Kakorlap Unit Usaha Pertanian"],
        normalize_data.LEDGER_HEADERS,
        ["16 Nov 2026", "Produk", "Catatan", "2", "kg", "Rp 10.000,00", "", "", ""],
        ["", "", "TOTAL BULAN November 2026", "", "", "", "", "", ""],
    ]
    with patch.object(normalize_data.sheet_repo, "get_sheet", return_value=worksheet), \
         patch.object(normalize_data.sheet_repo, "_retry_google_call", side_effect=lambda operation, *args, **kwargs: operation(*args, **kwargs)), \
         patch.object(normalize_data.sheet_repo, "invalidate_cache"), \
         patch.object(normalize_data, "_write_backup", return_value="backup.json"), \
         patch.object(normalize_data.periodic_total_service, "process_all_units_period", return_value={}) as process_totals:
        result = normalize_data.normalize_sheet("(3)Pertanian ", apply=True)

    worksheet.spreadsheet.batch_update.assert_called_once()
    request_bundle = worksheet.spreadsheet.batch_update.call_args.args[0]["requests"]
    assert any("updateCells" in request for request in request_bundle)
    assert result["status"] == "updated"
    assert result["removed_totals"] == 1
    assert process_totals.call_count == 5
    assert all(call.kwargs["force"] is True for call in process_totals.call_args_list)


def test_rewrite_clears_legacy_columns_after_transaction_id():
    worksheet = Mock()
    worksheet.id = 456
    worksheet.get_all_values.return_value = []
    plan = {
        "sheet": "(3)Pertanian ",
        "header_index": 0,
        "headers": normalize_data.LEDGER_HEADERS,
        "rows": [],
        "raw_values": [normalize_data.LEDGER_HEADERS + ["legacy spill"]],
    }
    with patch.object(normalize_data.sheet_repo, "get_sheet", return_value=worksheet), \
         patch.object(normalize_data.sheet_repo, "_retry_google_call", side_effect=lambda operation, *args, **kwargs: operation(*args, **kwargs)), \
         patch.object(normalize_data.sheet_repo, "invalidate_cache"):
        normalize_data._rewrite_sheet(plan)

    requests = worksheet.spreadsheet.batch_update.call_args.args[0]["requests"]
    extra_header_clear = next(request["updateCells"]["range"] for request in requests if "updateCells" in request and request["updateCells"].get("range", {}).get("startColumnIndex") == 9)
    assert extra_header_clear["endColumnIndex"] == 10


def test_period_processor_forces_totals_only_for_requested_units():
    from app.services import periodic_total_service

    with patch.object(periodic_total_service, "write_period_total") as write_week, \
         patch.object(periodic_total_service, "write_monthly_total") as write_month:
        results = periodic_total_service.process_all_units_period(
            2026, 11, period_kind="week", week_index=2,
            force=True, unit_names=["(3)Pertanian "],
        )

    write_week.assert_called_once()
    assert write_week.call_args.args[0] == "(3)Pertanian "
    assert write_week.call_args.kwargs["force"] is True
    write_month.assert_not_called()
    assert list(results) == ["(3)Pertanian "]


def test_period_processor_defaults_to_configured_ledger_units():
    from app.services import periodic_total_service

    with patch.object(periodic_total_service.config, "UNIT_USAHA", ["Unit A"]), \
         patch.object(periodic_total_service, "write_period_total") as write_week:
        results = periodic_total_service.process_all_units_period(
            2026, 11, period_kind="week", week_index=1,
        )

    write_week.assert_called_once()
    assert write_week.call_args.args[0] == "Unit A"
    assert list(results) == ["Unit A"]


def test_period_processor_continues_after_unit_failure():
    from app.services import periodic_total_service

    success = periodic_total_service.PeriodTotal(
        periodic_total_service.get_period_boundaries(2026, 11)[0], 0, 0, 0,
    )
    with patch.object(periodic_total_service.config, "UNIT_USAHA", ["Unit A", "Unit B"]), \
         patch.object(periodic_total_service, "write_period_total", side_effect=[RuntimeError("failed"), success]) as write_week:
        results = periodic_total_service.process_all_units_period(
            2026, 11, period_kind="week", week_index=1,
        )

    assert isinstance(results["Unit A"], RuntimeError)
    assert results["Unit B"] == success
    assert write_week.call_count == 2