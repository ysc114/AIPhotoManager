# core/ai_organizer.py
"""
AI 智能整理 - 统一编排层 V4
串联三级分类 → 角色聚合 → 壁纸评分，不包含 UI 代码。
"""

import time
from config.labels import LABEL_MAP
from core.analysis_cache import get_cache


class _OrganizeCancelled(Exception):
    pass


class AIOrganizer:

    def __init__(self):
        self._stop_flag = False
        self._classifier = None
        self._identity_manager = None
        self.cache = get_cache()
        self._cancelled = None

    def stop(self):
        self._stop_flag = True

    def _should_stop(self):
        return self._stop_flag or bool(self._cancelled and self._cancelled())

    def organize_folder(self, image_paths, progress_callback=None, cancelled=None):
        self._stop_flag = False
        self._cancelled = cancelled
        start_time = time.time()
        total = len(image_paths)

        result = {
            "total": total,
            "success": 0,
            "failed": 0,
            "time_cost": 0,
            "cancelled": False,
            "categories": {},
            "images": [],
            "characters": [],
            "wallpapers": [],
            "errors": [],
        }

        cat_result = self._step_classify(image_paths, progress_callback)
        if cat_result.get("cancelled"):
            result["cancelled"] = True
            result["time_cost"] = round(time.time() - start_time, 1)
            return result

        result["categories"] = cat_result.get("categories", {})
        result["images"] = cat_result.get("images", [])
        result["success"] = cat_result.get("success", 0)
        result["failed"] = cat_result.get("failed", 0)

        char_result = self._step_characters(result["images"], progress_callback)
        if char_result.get("cancelled"):
            result["cancelled"] = True
            result["time_cost"] = round(time.time() - start_time, 1)
            return result

        result["characters"] = char_result.get("characters", [])
        result["errors"] = cat_result.get("errors", []) + char_result.get("errors", [])
        result["wallpapers"] = self._step_wallpapers(result["images"])
        result["time_cost"] = round(time.time() - start_time, 1)

        if progress_callback:
            progress_callback("done", "AI智能整理完成", 100)

        return result

    def _step_classify(self, image_paths, progress_callback):
        if self._should_stop():
            return {"cancelled": True}
        if progress_callback:
            progress_callback("classify", "正在AI分类...", 0)

        self._ensure_classifier()
        categories = {}
        images = []
        success = 0
        failed = 0
        errors = []
        total = len(image_paths)

        for idx, path in enumerate(image_paths):
            if self._should_stop():
                return {"cancelled": True}
            try:
                cached = self.cache.get(path)
                if cached is not None:
                    category_en = cached.get("category", "未知")
                    category_cn = LABEL_MAP.get(category_en, category_en)
                    quality = cached.get("quality", 0)
                    l1_cached = cached.get("_category_cn")
                else:
                    result = self._classifier.analyze(path)
                    category_en = result.get("category", "未知")
                    category_cn = LABEL_MAP.get(category_en, category_en)
                    quality = result.get("quality", 0)
                    l1_cached = None
                cat_name = l1_cached if l1_cached else category_cn
                has_feedback = bool(l1_cached)
                categories[cat_name] = categories.get(cat_name, 0) + 1
                images.append({"path": path, "category": cat_name, "quality": quality, "character_id": None, "feedback": has_feedback})
                success += 1
            except Exception as exc:
                categories["分析失败"] = categories.get("分析失败", 0) + 1
                images.append({"path": path, "category": "分析失败", "quality": 0, "character_id": None, "feedback": False})
                failed += 1
                errors.append(f"{path}: {exc}")
            if progress_callback and idx % 5 == 0:
                progress_callback("classify", f"AI分类 {idx+1}/{total}", int((idx+1)/total*60))

        return {"categories": dict(sorted(categories.items(), key=lambda x: x[1], reverse=True)), "images": images, "success": success, "failed": failed, "errors": errors}

    def _step_characters(self, images, progress_callback):
        if progress_callback:
            progress_callback("characters", "正在识别角色...", 65)
        if self._should_stop():
            return {"cancelled": True}

        person_paths = [img.get("path") for img in images if img.get("category") and ("兽装" in str(img.get("category")) or "人物" in str(img.get("category")))]
        if not person_paths:
            return {"characters": []}

        self._ensure_character_manager()
        characters = []

        def identity_progress(current, total, status):
            if self._should_stop():
                raise _OrganizeCancelled()
            if progress_callback:
                percent = 65 + int(current / max(1, total) * 30)
                progress_callback("characters", status, percent)

        try:
            raw_groups = self._identity_manager.analyze_folder(
                person_paths, progress_callback=identity_progress) or []
            for g in raw_groups:
                if g is None:
                    continue
                characters.append({"character_id": g.get("character_id", ""), "name": g.get("name", ""), "type": g.get("type", ""), "cover": g.get("cover_image", ""), "images": g.get("images", []), "count": g.get("count", 0)})
        except _OrganizeCancelled:
            return {"cancelled": True}
        except Exception as e:
            return {"characters": [], "errors": [f"角色聚合失败: {e}"]}

        char_map = {}
        for c in characters:
            for img_path in c.get("images", []):
                char_map[img_path.replace("\\", "/")] = c.get("character_id", "")
        for img in images:
            key = img.get("path", "").replace("\\", "/")
            if key in char_map:
                img["character_id"] = char_map[key]
        return {"characters": characters}

    def _step_wallpapers(self, images):
        return []

    def _ensure_classifier(self):
        if self._classifier is None:
            from core.ai_classifier import AIClassifier
            self._classifier = AIClassifier()

    def _ensure_character_manager(self):
        if self._identity_manager is None:
            from core.identity import IdentityManager
            self._identity_manager = IdentityManager()

    def close(self):
        if self._identity_manager is not None:
            self._identity_manager.close()
