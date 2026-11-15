import unittest
from unittest.mock import patch

from app.services import karyawan_service


class DeleteKaryawanCommandTests(unittest.TestCase):
    def setUp(self):
        self.employee = {"nik": "001", "nama": "Budi", "row": 2}

    @patch.object(karyawan_service, "is_admin", return_value=True)
    @patch.object(karyawan_service, "get_karyawan")
    @patch.object(karyawan_service.sheet_repo, "delete_row")
    def test_requires_confirmation_before_deleting(self, delete_row, get_karyawan, _is_admin):
        get_karyawan.return_value = self.employee

        reply = karyawan_service.handle_karyawan("hapus karyawan 1", "628123")

        self.assertIn("hapus karyawan 001 konfirmasi", reply)
        delete_row.assert_not_called()

    @patch.object(karyawan_service, "is_admin", return_value=True)
    @patch.object(karyawan_service, "get_karyawan")
    @patch.object(karyawan_service.sheet_repo, "delete_row")
    def test_confirmation_deletes_employee_profile(self, delete_row, get_karyawan, _is_admin):
        get_karyawan.return_value = self.employee

        reply = karyawan_service.handle_karyawan("hapus karyawan 001 konfirmasi", "628123")

        self.assertIn("Budi", reply)
        self.assertIn("Riwayat presensi tetap tersimpan", reply)
        delete_row.assert_called_once_with(karyawan_service.EMPLOYEE_SHEET, 2)

    @patch.object(karyawan_service, "is_admin", return_value=False)
    @patch.object(karyawan_service, "get_karyawan")
    @patch.object(karyawan_service.sheet_repo, "delete_row")
    def test_non_admin_cannot_delete(self, delete_row, get_karyawan, _is_admin):
        reply = karyawan_service.handle_karyawan("hapus karyawan 001 konfirmasi", "628123")

        self.assertEqual(reply, karyawan_service.MSG_ADMIN_ONLY)
        get_karyawan.assert_not_called()
        delete_row.assert_not_called()


if __name__ == "__main__":
    unittest.main()