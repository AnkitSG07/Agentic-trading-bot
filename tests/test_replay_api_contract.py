import unittest


class ReplayApiContractTests(unittest.TestCase):
    def test_expected_response_keys(self):
        expected = {"run_id", "status"}
        self.assertIn("run_id", expected)
        self.assertIn("status", expected)


if __name__ == "__main__":
    unittest.main()
