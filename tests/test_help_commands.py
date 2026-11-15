import unittest
from unittest.mock import call, patch

from app.services import karyawan_service, sales_service
from app.routers import webhook


class HelpCommandTests(unittest.TestCase):
    def test_sales_help_includes_copy_ready_messages(self):
        reply = sales_service.handle_help_command("help")

        self.assertIn("Contoh siap salin dikirim sebagai pesan terpisah", reply)
        self.assertEqual(len(sales_service.help_copy_messages()), 3)
        self.assertNotIn("SALIN & KIRIM PESAN", reply)

    def test_employee_help_includes_copy_ready_messages(self):
        reply = karyawan_service._help_text(admin=False)

        self.assertIn("Contoh siap salin dikirim sebagai pesan terpisah", reply)
        self.assertEqual(karyawan_service.help_copy_messages()[0], "masuk")
        self.assertEqual(karyawan_service.help_copy_messages(admin=True)[0], "masuk 001")

    def test_help_examples_are_sent_as_separate_plain_messages(self):
        with patch.object(webhook, "send_whatsapp", return_value=True) as send:
            sent = webhook._send_help_reply("628123", "Panduan", ["masuk", "pulang"])

        self.assertTrue(sent)
        self.assertEqual(send.call_args_list, [
            call("628123", "Panduan"),
            call("628123", "📋 *PESAN SIAP DISALIN*"),
            call("628123", "masuk"),
            call("628123", "pulang"),
        ])


if __name__ == "__main__":
    unittest.main()