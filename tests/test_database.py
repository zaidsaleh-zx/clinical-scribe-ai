import os
import tempfile
import unittest
from unittest.mock import patch

from backend import db


class DeleteSessionTests(unittest.TestCase):
    def test_delete_removes_visit_data_and_keeps_shared_patient_until_last_visit(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "test.db")
            with patch.object(db, "DB_PATH", database_path):
                db.init_db()
                patient_id = db.create_patient({"first_name": "Synthetic", "last_name": "Patient"})
                first_id = db.create_consultation(patient_id, transcript="synthetic transcript")
                second_id = db.create_consultation(patient_id, transcript="second synthetic visit")
                db.save_soap_note(first_id, {"subjective": ["synthetic note"]})
                order_id = db.create_pharmacy_order(
                    first_id,
                    patient_id,
                    "paracetamol",
                    dosage="500mg",
                )

                self.assertTrue(db.delete_session(first_id))
                self.assertIsNone(db.get_consultation(first_id))
                self.assertIsNone(db.get_soap_note(first_id))
                self.assertEqual(db.get_patient(patient_id)["first_name"], "Synthetic")

                connection = db.get_conn()
                try:
                    self.assertIsNone(connection.execute(
                        "SELECT id FROM pharmacy_orders WHERE id = ?",
                        (order_id,),
                    ).fetchone())
                    self.assertIsNone(connection.execute(
                        "SELECT id FROM audit_log WHERE record_id IN (?, ?)",
                        (first_id, order_id),
                    ).fetchone())
                finally:
                    connection.close()

                self.assertTrue(db.delete_session(second_id))
                self.assertIsNone(db.get_patient(patient_id))
                self.assertFalse(db.delete_session(second_id))


if __name__ == "__main__":
    unittest.main()
