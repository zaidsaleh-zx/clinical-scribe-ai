import unittest

from backend.auth import create_session_token, is_valid_session_token
from backend.order_routing import preview_orders
from backend.soap_generator import _classify_line


class AuthenticationTokenTests(unittest.TestCase):
    def test_session_token_expires_and_is_bound_to_secret(self):
        token = create_session_token("secret", now=1000)
        self.assertTrue(is_valid_session_token("secret", token, now=1001))
        self.assertFalse(is_valid_session_token("wrong-secret", token, now=1001))
        self.assertFalse(is_valid_session_token("secret", token, now=1000 + 8 * 60 * 60))
        self.assertFalse(is_valid_session_token("secret", "malformed"))
        self.assertFalse(is_valid_session_token("secret", "1000.🩺", now=1001))


class SoapClassificationTests(unittest.TestCase):
    def test_representative_sections_and_conservative_fallback(self):
        cases = [
            ("Patient has a headache for 3 days", "patient", "subjective"),
            ("Blood pressure is 120/80", "doctor", "objective"),
            ("The assessment is likely viral infection", "doctor", "assessment"),
            ("I will prescribe paracetamol 500mg", "doctor", "plan"),
            ("Good morning, what brings you in today?", "doctor", "unclassified"),
            ("No chest pain", "patient", "subjective"),
        ]
        for text, speaker, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(_classify_line(text, speaker), expected)


class OrderSuggestionTests(unittest.TestCase):
    def test_only_actionable_non_negated_items_are_suggested(self):
        preview = preview_orders({
            "plan": [
                "Start paracetamol 500mg twice daily for 3 days.",
                "Do not prescribe aspirin.",
                "There is no need to start ibuprofen.",
                "CBC was normal last month.",
                "Order CBC and repeat in one week.",
            ],
            "objective": ["Temperature is 38 C."],
        })
        self.assertEqual(len(preview["medications"]), 1)
        self.assertEqual(preview["medications"][0]["medication"], "paracetamol")
        self.assertEqual(preview["medications"][0]["dosage"], "500mg")
        self.assertEqual([item["test_name"] for item in preview["laboratory_tests"]], ["Cbc"])

    def test_mention_without_action_does_not_create_order_suggestion(self):
        preview = preview_orders({
            "plan": ["The patient currently takes metformin."],
            "objective": ["CBC results are normal."],
        })
        self.assertEqual(preview["medications"], [])
        self.assertEqual(preview["laboratory_tests"], [])


if __name__ == "__main__":
    unittest.main()
