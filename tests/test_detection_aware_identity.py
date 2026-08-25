import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.identity.database import IdentityDatabase
from core.identity.manager import IdentityManager


class DetectionAwareIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "identity_db.sqlite")
        self.db = IdentityDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_merge_preserves_detection_fields_and_same_photo_membership(self):
        target = self.db.create_group(group_type="fursuit_character")
        source = self.db.create_group(group_type="fursuit_character")
        image_path = "photos/group.jpg"
        target_embedding = np.arange(4, dtype=np.float32)
        source_embedding = np.arange(4, 8, dtype=np.float32)

        self.db.add_image(
            target,
            image_path,
            embedding=target_embedding,
            embedding_type="fursuit_fursee",
            bbox=[1, 2, 30, 40],
            layer1_category="兽装",
            confidence=0.91,
            detection_index=0,
        )
        self.db.add_image(
            source,
            image_path,
            embedding=source_embedding,
            embedding_type="fursuit_fursee",
            bbox=[50, 60, 90, 100],
            layer1_category="兽装",
            confidence=0.82,
            detection_index=1,
        )

        result = self.db.merge_group_members(target, [source])

        self.assertEqual(result["moved"], 1)
        self.assertIsNotNone(self.db.get_group(target))
        self.assertIsNone(self.db.get_group(source))
        rows = self.db.get_images_by_group(target)
        self.assertEqual(
            {(row["image_path"], row["detection_index"]) for row in rows},
            {(image_path, 0), (image_path, 1)},
        )
        by_index = {row["detection_index"]: row for row in rows}
        self.assertEqual(by_index[1]["bbox"], "[50, 60, 90, 100]")
        self.assertEqual(by_index[1]["confidence"], 0.82)
        self.assertEqual(by_index[1]["embedding_type"], "fursuit_fursee")
        np.testing.assert_array_equal(
            np.frombuffer(by_index[1]["embedding"], dtype=np.float32),
            source_embedding,
        )

    def test_manager_merge_uses_detection_aware_database_operation(self):
        target = self.db.create_group(group_type="fursuit_character")
        source = self.db.create_group(group_type="fursuit_character")
        self.db.add_image(
            source,
            "photo.jpg",
            embedding=np.ones(3, dtype=np.float32),
            embedding_type="fursuit_fursee",
            bbox=[3, 4, 5, 6],
            confidence=0.77,
            detection_index=2,
        )
        self.db.close()

        manager = IdentityManager(db_path=self.db_path)
        try:
            result = manager.merge_groups(target, [source])
            self.assertEqual(result["moved"], 1)
            rows = manager.db.get_images_by_group(target)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["detection_index"], 2)
            self.assertEqual(rows[0]["bbox"], "[3, 4, 5, 6]")
            self.assertEqual(rows[0]["confidence"], 0.77)
        finally:
            manager.close()

    def test_schema_remains_v2(self):
        version = self.db.conn.execute("PRAGMA user_version").fetchone()[0]
        columns = [
            row[1]
            for row in self.db.conn.execute("PRAGMA table_info(identity_image)")
        ]
        self.assertEqual(version, 2)
        self.assertIn("detection_index", columns)
        self.assertIn("embedding", columns)

    def test_merge_rejects_legacy_and_fursee_mixing(self):
        target = self.db.create_group(group_type="fursuit_character")
        source = self.db.create_group(group_type="fursuit_character")
        self.db.add_image(
            target,
            "fursee.jpg",
            embedding=np.ones(3, dtype=np.float32),
            embedding_type="fursuit_fursee",
            detection_index=0,
        )
        self.db.add_image(
            source,
            "legacy.jpg",
            embedding=np.ones(3, dtype=np.float32),
            embedding_type="fursuit_visual",
            detection_index=0,
        )

        with self.assertRaisesRegex(ValueError, "Legacy 与 Fursee"):
            self.db.merge_group_members(target, [source])

        self.assertIsNotNone(self.db.get_group(source))
        self.assertEqual(len(self.db.get_images_by_group(source)), 1)

    def test_same_original_can_belong_to_multiple_groups_by_detection(self):
        first = self.db.create_group(group_type="fursuit_character")
        second = self.db.create_group(group_type="fursuit_character")
        image_path = "photos/multi-character.jpg"

        self.db.add_image(
            first,
            image_path,
            embedding=np.ones(4, dtype=np.float32),
            embedding_type="fursuit_fursee",
            bbox=[10, 20, 30, 40],
            detection_index=0,
        )
        self.db.add_image(
            second,
            image_path,
            embedding=np.full(4, 2, dtype=np.float32),
            embedding_type="fursuit_fursee",
            bbox=[50, 60, 70, 80],
            detection_index=1,
        )

        first_rows = self.db.get_images_by_group(first)
        second_rows = self.db.get_images_by_group(second)
        self.assertEqual(
            (first_rows[0]["image_path"], first_rows[0]["detection_index"]),
            (image_path, 0),
        )
        self.assertEqual(
            (second_rows[0]["image_path"], second_rows[0]["detection_index"]),
            (image_path, 1),
        )
        self.assertEqual(first_rows[0]["bbox"], "[10, 20, 30, 40]")
        self.assertEqual(second_rows[0]["bbox"], "[50, 60, 70, 80]")


if __name__ == "__main__":
    unittest.main()
