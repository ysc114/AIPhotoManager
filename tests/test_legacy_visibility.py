import unittest

from core.identity.manager import IdentityManager


class LegacyVisibilityTests(unittest.TestCase):
    def test_fursuit_page_only_shows_fursee(self):
        mgr = IdentityManager()
        try:
            groups = mgr.get_groups(group_type="fursuit_character")
            self.assertTrue(groups)
            self.assertTrue(all(
                all(det.get("embedding_type") == "fursuit_fursee"
                    for det in group.get("detections", []))
                for group in groups
            ))
        finally:
            mgr.close()

    def test_character_page_keeps_source_types(self):
        mgr = IdentityManager()
        try:
            groups = mgr.get_groups()
            self.assertTrue(any(
                "fursuit_visual" in (group.get("source_types") or [])
                for group in groups
            ))
            self.assertTrue(any(
                "fursuit_fursee" in (group.get("source_types") or [])
                for group in groups
            ))
        finally:
            mgr.close()


if __name__ == "__main__":
    unittest.main()
