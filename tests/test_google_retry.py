import random
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core import retry
from app.repositories import sheet_repo


class GoogleApiError(Exception):
    def __init__(self, status_code, headers=None):
        super().__init__("test error")
        self.response = SimpleNamespace(status_code=status_code, headers=headers or {})


class GoogleRetryTests(unittest.TestCase):
    def test_retries_429_then_succeeds(self):
        operation = Mock(side_effect=[GoogleApiError(429), "ok"])

        with patch.object(retry.time, "sleep") as sleep:
            result = retry.retry_google_api(
                max_attempts=3, base_delay=1, jitter=False
            )(operation)()

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_500_raises_after_max_attempts(self):
        operation = Mock(side_effect=GoogleApiError(500))

        with patch.object(retry.time, "sleep"):
            with self.assertRaises(GoogleApiError):
                retry.retry_google_api(
                    max_attempts=3, base_delay=0, jitter=False
                )(operation)()

        self.assertEqual(operation.call_count, 3)

    def test_append_timeout_is_not_repeated_when_id_is_already_present(self):
        transaction_id = "20261115-PRT-0123abcd"
        operation = Mock(side_effect=TimeoutError("response lost"))
        find_existing = Mock(return_value={transaction_id})

        with patch.object(retry.time, "sleep") as sleep:
            result = retry.safe_append_with_idempotency(
                operation,
                [transaction_id],
                find_existing,
                max_attempts=4,
            )

        self.assertIsNone(result)
        operation.assert_called_once()
        find_existing.assert_called_once_with([transaction_id])
        sleep.assert_not_called()

    def test_retry_after_header_sets_retry_delay(self):
        operation = Mock(side_effect=[
            GoogleApiError(429, {"Retry-After": "4.5"}),
            "ok",
        ])

        with patch.object(retry.time, "sleep") as sleep:
            result = retry.retry_google_api(
                max_attempts=2, base_delay=1, jitter=True
            )(operation)()

        self.assertEqual(result, "ok")
        sleep.assert_called_once_with(4.5)

    def test_jitter_varies_delay_using_random_value(self):
        error = GoogleApiError(503)
        prior_state = random.getstate()
        try:
            random.seed(41)
            first_run = [retry._retry_delay(error, 0, 1.0, 30.0, True) for _ in range(3)]
            random.seed(41)
            second_run = [retry._retry_delay(error, 0, 1.0, 30.0, True) for _ in range(3)]
        finally:
            random.setstate(prior_state)

        self.assertEqual(first_run, second_run)
        self.assertGreater(len(set(first_run)), 1)

    def test_atomic_append_checks_ids_before_retrying_timeout(self):
        transaction_id = "20261115-PRT-0123abcd"
        worksheet = Mock(id=7)
        spreadsheet = Mock()
        spreadsheet.batch_update.side_effect = [GoogleApiError(429), None]

        with patch.object(sheet_repo, "get_spreadsheet", return_value=spreadsheet), \
             patch.object(sheet_repo, "get_sheet", return_value=worksheet), \
             patch.object(sheet_repo, "_find_existing_transaction_ids", return_value=set()), \
             patch.object(sheet_repo, "invalidate_cache"), \
             patch.object(retry.time, "sleep"):
            sheet_repo.append_rows_atomically([
                ("(3)Pertanian ", ["N", "2026-11-15", "Produk", transaction_id]),
            ])

        self.assertEqual(spreadsheet.batch_update.call_count, 2)


if __name__ == "__main__":
    unittest.main()