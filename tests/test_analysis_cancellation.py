"""Cancellation contract for incremental identity analysis entry points."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from core.identity.manager import IdentityManager


class AnalysisCancellationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="analysis_cancel_")
        self.photos = os.path.join(self.root, "photos")
        os.makedirs(self.photos)
        self.manager = IdentityManager(db_path=os.path.join(self.root, "id.db"))

    def tearDown(self):
        self.manager.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _photo(self, name):
        path = os.path.join(self.photos, name)
        with open(path, "wb") as handle:
            handle.write(name.encode("utf-8"))
        return path.replace("\\", "/")

    def _patch_identity(self, manager=None):
        manager = manager or self.manager
        l1 = {"category": "scenery", "label_cn": "风景", "quality": 1.0}
        return mock.patch.object(manager.embedder, "get_l1_info", return_value=l1), \
            mock.patch.object(manager.embedder, "route_l1", return_value=None)

    def test_analyze_paths_pre_cancel_is_side_effect_free(self):
        paths = [self._photo("a.jpg"), self._photo("b.jpg")]
        with self._patch_identity()[0] as get_l1, self._patch_identity()[1] as route, \
                mock.patch.object(self.manager, "_process_single_image") as process, \
                mock.patch.object(self.manager.cluster, "incremental_assign") as assign:
            result = self.manager.analyze_paths(paths, cancelled=lambda: True)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["new"], 0)
        process.assert_not_called()
        assign.assert_not_called()
        get_l1.assert_not_called()
        route.assert_not_called()

    def test_analyze_paths_cancels_between_images_and_can_rerun(self):
        paths = [self._photo("a.jpg"), self._photo("b.jpg"), self._photo("c.jpg")]
        state = {"done": 0}

        def process(path):
            state["done"] += 1
            self.manager.db.add_image("", path, embedding_type="face")

        def cancelled():
            return state["done"] >= 1

        with self._patch_identity()[0], self._patch_identity()[1], \
                mock.patch.object(self.manager, "_process_single_image", side_effect=process), \
                mock.patch.object(self.manager.cluster, "incremental_assign", return_value={
                    "joined": 0, "created": 0, "conflicts": []}) as assign:
            result = self.manager.analyze_paths(paths, cancelled=cancelled)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["new"], 1)
        self.assertEqual(state["done"], 1)
        self.assertEqual(assign.call_count, 2, "Completed rows still require incremental assignment")

        # A later invocation can process the remaining files normally.
        with self._patch_identity()[0], self._patch_identity()[1], \
                mock.patch.object(self.manager, "_process_single_image", side_effect=process), \
                mock.patch.object(self.manager.cluster, "incremental_assign", return_value={
                    "joined": 0, "created": 0, "conflicts": []}):
            rerun = self.manager.analyze_paths(paths)
        self.assertEqual(rerun["new"], 2)
        self.assertEqual(rerun["dup_path"], 1)
        self.assertEqual(state["done"], 3)
        self.assertNotIn("cancelled", rerun, "Old callers keep the original result contract")

    def test_analyze_paths_cancel_during_l1_does_not_start_identity_model(self):
        path = self._photo("a.jpg")
        stop = []

        def l1(_path):
            stop.append(True)
            return {"category": "fursuit"}

        with mock.patch.object(self.manager.embedder, "get_l1_info", side_effect=l1), \
                mock.patch.object(self.manager, "_process_single_image") as process, \
                mock.patch.object(self.manager.cluster, "incremental_assign") as assign:
            result = self.manager.analyze_paths([path], cancelled=lambda: bool(stop))
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["new"], 0)
        self.assertEqual(result["completed"], 0)
        process.assert_not_called()
        assign.assert_not_called()

    def test_analyze_paths_cancel_during_dedup_stops_before_inference(self):
        path = self._photo("a.jpg")
        existing = self._photo("existing.jpg")
        self.manager.db.add_image("", existing, embedding_type="face")
        hashed = []

        def md5(value):
            hashed.append(value)
            return value

        with mock.patch("core.duplicates.cached_md5", side_effect=md5), \
                mock.patch.object(self.manager, "_process_single_image") as process:
            result = self.manager.analyze_paths([path], cancelled=lambda: bool(hashed))
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["completed"], 0)
        self.assertEqual(hashed, [path])
        process.assert_not_called()

    def test_analyze_paths_single_failure_continues(self):
        paths = [self._photo("a.jpg"), self._photo("b.jpg")]
        with self._patch_identity()[0], self._patch_identity()[1], \
                mock.patch.object(self.manager, "_process_single_image", side_effect=[
                    RuntimeError("bad image"), None]) as process, \
                mock.patch.object(self.manager.cluster, "incremental_assign", return_value={}):
            result = self.manager.analyze_paths(paths, cancelled=lambda: False)
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["new"], 2)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(process.call_count, 2)

    def test_analyze_new_photos_pre_cancel_is_side_effect_free(self):
        self._photo("a.jpg")
        with mock.patch.object(self.manager, "_process_single_image") as process, \
                mock.patch.object(self.manager.cluster, "incremental_assign") as assign:
            result = self.manager.analyze_new_photos(self.photos, cancelled=lambda: True)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["new"], 0)
        self.assertEqual(result["completed"], 0)
        process.assert_not_called()
        assign.assert_not_called()

    def test_analyze_new_photos_cancel_and_resume_preserves_processed_rows(self):
        paths = [self._photo("a.jpg"), self._photo("b.jpg")]
        processed = []

        def process(path):
            processed.append(path)
            self.manager.db.add_image("", path, embedding_type="face")

        with mock.patch.object(self.manager, "_process_single_image", side_effect=process), \
                mock.patch.object(self.manager.cluster, "incremental_assign", return_value={
                    "joined": 0, "created": 0, "conflicts": []}) as assign:
            result = self.manager.analyze_new_photos(
                self.photos, cancelled=lambda: bool(processed))
            self.assertTrue(result["cancelled"])
            self.assertEqual(result["new"], 1)
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["skipped"], 0, "Unprocessed files are not skips")
            self.assertEqual(assign.call_count, 2)
            rerun = self.manager.analyze_new_photos(self.photos)
        self.assertEqual(rerun["new"], 1)
        self.assertEqual(rerun["skipped"], 1)
        self.assertEqual(processed, paths)
        self.assertNotIn("cancelled", rerun)

    def test_analyze_new_photos_single_failure_continues_and_cancel_reports_count(self):
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            self._photo(name)
        state = {"done": 0}

        def process(path):
            state["done"] += 1
            if path.endswith("b.jpg"):
                raise RuntimeError("bad image")

        with mock.patch.object(self.manager, "_process_single_image", side_effect=process), \
                mock.patch.object(self.manager.cluster, "incremental_assign", return_value={
                    "joined": 0, "created": 0, "conflicts": []}) as assign:
            result = self.manager.analyze_new_photos(
                self.photos, cancelled=lambda: state["done"] >= 3)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["new"], 3)
        self.assertEqual(state["done"], 3)
        self.assertEqual(assign.call_count, 2)


if __name__ == "__main__":
    unittest.main()
