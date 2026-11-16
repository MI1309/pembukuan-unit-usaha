import unittest
from datetime import date
from unittest.mock import Mock, patch

from app.config import config
from app.core.transaction_id import generate_transaction_id
from app.migrate_tx_id import migrate_transaction_ids
from app.repositories import sheet_repo
from app.routers import webhook
from app.services import sales_service
from app import setup_reference_structure


class TransactionIdTests(unittest.TestCase):
    def setUp(self):
        self.sale = {
            "produk": "Telur Ayam",
            "kategori": "TELUR",
            "satuan": 30000,
            "unit_kuantitas": "KG",
            "kuantitas": 2,
            "penjualan": 60000,
            "is_penjualan": True,
            "keterangan": "Pesanan pagi",
            "tanggal": date(2026, 11, 15),
            "unit": "(4)Ternak Ayam Fazza",
        }

    def test_generator_is_deterministic_and_has_expected_shape(self):
        first = generate_transaction_id(
            "628123", date(2026, 11, 15), "Telur Ayam", "2 KG", 60000,
            "Pesanan pagi", "(4)Ternak Ayam Fazza",
        )
        second = generate_transaction_id(
            "628123", date(2026, 11, 15), "Telur Ayam", "2 KG", 60000,
            "Pesanan pagi", "(4)Ternak Ayam Fazza",
        )
        localized_date = generate_transaction_id(
            "628123", "15 November 2026", "Telur Ayam", "2 KG", 60000,
            "Pesanan pagi", "(4)Ternak Ayam Fazza",
        )

        self.assertEqual(first, second)
        self.assertEqual(first, localized_date)
        self.assertRegex(first, r"^20261115-TAF-[a-f0-9]{8}$")

    def test_unit_sheet_mapping_keeps_emran_separate_from_fazza(self):
        self.assertEqual(sales_service.get_unit_sheet("ayam emran"), "(5)Ternak Ayam Emran")
        self.assertEqual(sales_service.get_unit_sheet("telur ayam fazza"), "(4)Ternak Ayam Fazza")
        self.assertEqual(sales_service.get_unit_sheet("telur ayam emran"), "(5)Ternak Ayam Emran")
        self.assertEqual(sales_service.get_unit_sheet("ayam fazza"), "(4)Ternak Ayam Fazza")

    def test_ledger_header_uses_transaction_id_label(self):
        self.assertEqual(setup_reference_structure.LEDGER_HEADERS[-1], "Transaction ID")
        self.assertEqual(setup_reference_structure.LEDGER_HEADERS[0], "Tanggal")

    def test_unit_row_uses_canonical_ledger_layout(self):
        sale = {
            "tanggal": date(2026, 11, 15),
            "produk": "Telur Ayam Emran",
            "keterangan": "Jual ke P3",
            "kuantitas": 2,
            "unit_kuantitas": "KG",
            "satuan": 30000,
            "penjualan": 60000,
            "is_penjualan": True,
            "transaction_id": "20261115-TAE-1234abcd",
            "unit": "(5)Ternak Ayam Emran",
        }
        row = sales_service._save_sale(sale)[0][1]
        self.assertEqual(row[0], date(2026, 11, 15))
        self.assertEqual(row[-1], "20261115-TAE-1234abcd")
        self.assertEqual(row[1], "Telur Ayam Emran")

    def test_sort_rows_by_date_keeps_oldest_dates_first(self):
        rows = [
            ["", "2026-11-15", "Telur", "", "2", "KG", "60000", "", "60000", "aaa"],
            ["", "2026-11-02", "Telur", "", "3", "KG", "90000", "", "90000", "bbb"],
            ["", "2026-11-10", "Telur", "", "4", "KG", "120000", "", "120000", "ccc"],
        ]

        result = sheet_repo._sort_rows_by_date(rows, 2)
        self.assertEqual([row[-1] for row in result], ["bbb", "ccc", "aaa"])

    def test_lookup_does_not_treat_legacy_row_without_id_as_duplicate(self):
        worksheet = Mock()
        worksheet.get_all_values.return_value = [
            ["Nomor", "Tanggal", "Produk", "Transaction ID"],
            ["INV-1", "2026-11-15", "Telur Ayam"],
        ]
        tx_id = "20261115-TAF-0123abcd"

        with patch.object(sheet_repo, "get_sheet", return_value=worksheet):
            result = sheet_repo.find_transaction_ids(["ledger"], [tx_id])

        self.assertEqual(result, {tx_id: []})

    def test_repeated_content_without_event_id_is_skipped(self):
        tx_id = sales_service.generate_transaction_id(
            "628123", self.sale["tanggal"], self.sale["produk"], "2 KG", 60000,
            self.sale["keterangan"], self.sale["unit"],
        )
        lookup_results = [{tx_id: []}, {tx_id: [self.sale["unit"]]}]
        append = Mock()
        log_event = Mock()

        with patch.object(sales_service, "analyze_sales", return_value={"transaksi": [{}]}), \
             patch.object(sales_service, "_normalise_sale", side_effect=lambda raw: (dict(self.sale), None)), \
             patch.object(sheet_repo, "find_transaction_ids", side_effect=lookup_results), \
             patch.object(sheet_repo, "append_rows_atomically", append), \
             patch.object(sales_service, "_maybe_archive_unit_transactions"), \
             patch.object(sales_service, "_maybe_archive_daily_detail"), \
             patch("app.services.webhook_state.log_event", log_event):
            first = sales_service.process_sales("2 kg telur 60000", sender="628123")
            second = sales_service.process_sales("2 kg telur 60000", sender="628123")

        self.assertTrue(first[0])
        self.assertFalse(second[0])
        self.assertIn(tx_id, second[1])
        self.assertEqual(second[2]["duplicates"][0]["transaction_id"], tx_id)
        copy_message = sales_service.duplicate_copy_message(second[2]["duplicates"][0])
        self.assertIn("sudah tercatat", copy_message)
        self.assertIn(tx_id, copy_message)
        append.assert_called_once()
        self.assertEqual(append.call_args.args[0][0][1][-1], tx_id)
        log_event.assert_called_once_with(
            "628123", "2 kg telur 60000", "DUPLICATE_SKIPPED", invoice=tx_id
        )

    def test_webhook_redelivery_with_same_event_id_does_not_reprocess(self):
        sales_call = Mock(return_value=(True, "reply", {"saved": 1}))
        with patch.object(config, "WEBHOOK_SECRET", "test-secret"), \
               patch.object(webhook.threading, "Thread") as mock_thread, \
             patch.object(webhook.webhook_state, "is_duplicate_event", side_effect=[False, True]), \
             patch.object(webhook.karyawan_service, "normalize_phone", return_value="628123"), \
             patch.object(webhook.karyawan_service, "is_admin", return_value=True), \
             patch.object(webhook.karyawan_service, "is_help_command", return_value=False), \
             patch.object(webhook.karyawan_service, "handle_karyawan", return_value=None), \
             patch.object(webhook.webhook_state, "is_rate_limited", return_value=False), \
             patch.object(webhook.payroll_service, "handle_payroll_command", return_value=None), \
             patch.object(webhook.sales_service, "handle_help_command", return_value=None), \
             patch.object(webhook.sales_service, "process_rekap", return_value=None), \
             patch.object(webhook.sales_service, "process_sales", sales_call), \
             patch.object(webhook.webhook_state, "get_pending_confirmation", return_value=None), \
             patch.object(webhook.webhook_state, "log_event"), \
             patch.object(webhook, "send_whatsapp", return_value=True):
            payload = {
                "secret": "test-secret", "id": "gateway-001",
                "sender": "628123", "message": "catat penjualan telur pagi",
            }
            first = webhook.process_webhook_payload(payload)
            second = webhook.process_webhook_payload(payload)

        self.assertEqual(first["status"], "saved")
        self.assertEqual(second["status"], "duplicate")
        sales_call.assert_called_once()
        self.assertEqual(mock_thread.call_count, 2)
        self.assertTrue(all(call.kwargs["daemon"] for call in mock_thread.call_args_list))
        self.assertEqual(
            {call.kwargs["target"] for call in mock_thread.call_args_list},
            {webhook._safe_lazy_trigger, webhook._safe_refresh_current_totals},
        )

    def test_webhook_sends_forwardable_message_for_duplicate_transaction(self):
        duplicate_sale = {**self.sale, "transaction_id": "20261115-TAF-0123abcd"}
        send_message = Mock(return_value=True)
        with patch.object(config, "WEBHOOK_SECRET", "test-secret"), \
             patch.object(webhook.threading, "Thread"), \
             patch.object(webhook.webhook_state, "is_duplicate_event", return_value=False), \
             patch.object(webhook.karyawan_service, "normalize_phone", return_value="628123"), \
             patch.object(webhook.karyawan_service, "is_admin", return_value=True), \
             patch.object(webhook.karyawan_service, "is_help_command", return_value=False), \
             patch.object(webhook.webhook_state, "is_rate_limited", return_value=False), \
             patch.object(webhook.karyawan_service, "handle_karyawan", return_value=None), \
             patch.object(webhook.payroll_service, "handle_payroll_command", return_value=None), \
             patch.object(webhook.sales_service, "handle_help_command", return_value=None), \
             patch.object(webhook.sales_service, "process_rekap", return_value=None), \
             patch.object(webhook.webhook_state, "get_pending_confirmation", return_value=None), \
             patch.object(webhook.sales_service, "process_sales", return_value=(
                 False, "Transaksi sudah tercatat sebelumnya.", {"duplicates": [duplicate_sale]}
             )), \
             patch.object(webhook.webhook_state, "log_event"), \
             patch.object(webhook, "send_whatsapp", send_message):
            result = webhook.process_webhook_payload({
                "secret": "test-secret", "sender": "628123", "message": "catat transaksi",
            })

        self.assertEqual(result["status"], "duplicate")
        sent_messages = [call.args[1] for call in send_message.call_args_list]
        self.assertIn("Transaksi sudah tercatat sebelumnya.", sent_messages)
        self.assertIn("📋 *PESAN SIAP DITERUSKAN*", sent_messages)
        self.assertTrue(any("20261115-TAF-0123abcd" in text for text in sent_messages))

    def test_migration_dry_run_does_not_write_and_apply_appends_id_header(self):
        worksheet = Mock()
        worksheet.get_all_values.return_value = [
            ["Judul"],
            ["Nomor", "Tanggal", "Nama Barang", "Keterangan", "Kuantitas", "Satuan", "Pemasukan", "Pengeluaran", "Saldo"],
            ["", "2026-11-15", "Telur Ayam", "Pagi", "2 KG", "30000", "60000", "", "60000"],
        ]
        with patch("app.migrate_tx_id._migration_targets", return_value=[("(4)Ternak Ayam Fazza", 1, "ledger")]), \
             patch.object(sheet_repo, "get_sheet", return_value=worksheet):
            dry_run = migrate_transaction_ids(apply=False)
            worksheet.update_cell.assert_not_called()
            applied = migrate_transaction_ids(apply=True)

        self.assertEqual(dry_run["(4)Ternak Ayam Fazza"], 1)
        self.assertEqual(applied["(4)Ternak Ayam Fazza"], 1)
        self.assertEqual(worksheet.update_cell.call_count, 1)
        self.assertEqual(worksheet.update_cell.call_args_list[0].args, (2, 10, "Transaction ID"))
        worksheet.batch_update.assert_called_once()
        batch_updates = worksheet.batch_update.call_args.args[0]
        self.assertEqual(batch_updates[0]["range"], "J3")
        self.assertRegex(batch_updates[0]["values"][0][0], r"^20261115-TAF-[a-f0-9]{8}$")

    def test_migration_skips_rows_without_valid_transaction_date(self):
        worksheet = Mock()
        worksheet.get_all_values.return_value = [
            ["Judul"],
            ["Nomor", "Tanggal", "Nama Barang", "Keterangan", "Kuantitas", "Satuan", "Pemasukan", "Pengeluaran", "Saldo"],
            ["", "", "Baris ringkasan", "", "", "", "60000", "", "60000"],
            ["", "2026-11-15", "Telur Ayam", "Pagi", "2 KG", "30000", "60000", "", "60000"],
        ]
        with patch("app.migrate_tx_id._migration_targets", return_value=[("(4)Ternak Ayam Fazza", 1, "ledger")]), \
             patch.object(sheet_repo, "get_sheet", return_value=worksheet):
            counts = migrate_transaction_ids(apply=False)

        self.assertEqual(counts["(4)Ternak Ayam Fazza"], 1)
        worksheet.update_cell.assert_not_called()


if __name__ == "__main__":
    unittest.main()