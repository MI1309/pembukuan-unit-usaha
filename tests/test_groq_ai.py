import unittest

from app.core.groq_ai import _local_parse_regex


class LocalSalesParserTests(unittest.TestCase):
    def test_preserves_original_description_without_auto_parsed_prefix(self):
        parsed = _local_parse_regex("penj ayam fazza jual ke warung 1 10 ekor 15000")

        self.assertEqual(len(parsed["transaksi"]), 1)
        transaction = parsed["transaksi"][0]
        self.assertEqual(transaction["produk"], "ayam fazza")
        self.assertEqual(transaction["keterangan"], "jual ke warung 1")
        self.assertEqual(transaction["kuantitas"], "10")
        self.assertEqual(transaction["satuan"], "EKOR")
        self.assertEqual(transaction["harga_satuan"], 15000)


if __name__ == "__main__":
    unittest.main()
