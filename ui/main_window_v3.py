import os
import sys
import json
from pathlib import Path
from datetime import datetime

_project_root = Path(__file__).resolve().parents[1]

if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QStatusBar,
    QProgressBar,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QComboBox,
    QDialog,
    QSplitter,
)


from core.image_loader import load_images_from_folder
from core.ai_classifier import AIClassifier
from core.ai_advisor import AIAdvisor

from config.labels import LABEL_MAP


def get_human_categories():
    categories = []
    seen = set()
    for cn_name in LABEL_MAP.values():
        clean = cn_name
        for i in range(len(clean)):
            if ord(clean[i]) > 127:
                continue
            elif clean[i] == ' ':
                clean = clean[i+1:]
                break
            else:
                break
        if clean not in seen:
            seen.add(clean)
            categories.append(clean)
    return categories


# ============================================================
# 主窗口
# ============================================================

class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "AI Photo Manager V3.3"
        )

        self.resize(
            1400,
            850
        )

        self.image_list = []

        self.classifier = None

        self.current_image_path = None

        self.current_ai_category = None

        self.advisor = AIAdvisor()

        self.init_ui()

        self.connect_signal()

    def init_ui(self):

        central = QWidget()

        self.setCentralWidget(
            central
        )

        root = QVBoxLayout(
            central
        )

        self.title_label = QLabel(
            "AI Photo Manager V3.3"
        )

        self.title_label.setStyleSheet(
            "font-size:22px;font-weight:bold;"
        )

        root.addWidget(
            self.title_label
        )

        search_layout = QHBoxLayout()

        self.search_edit = QLineEdit()

        self.search_edit.setPlaceholderText(
            "输入关键词搜索"
        )

        self.btn_search = QPushButton(
            "🔍搜索"
        )

        search_layout.addWidget(
            self.search_edit
        )

        search_layout.addWidget(
            self.btn_search
        )

        root.addLayout(
            search_layout
        )

        # QSplitter 替代固定布局
        self.splitter = QSplitter(Qt.Horizontal)

        self.image_list_widget = QListWidget()
        self.image_list_widget.setIconSize(QSize(110, 110))
        self.image_list_widget.setMinimumWidth(200)

        self.splitter.addWidget(self.image_list_widget)

        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)

        self.splitter.addWidget(right_widget)
        self.splitter.setSizes([350, 1050])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        root.addWidget(self.splitter)

        self.preview_label = QLabel(
            "请选择图片"
        )

        self.preview_label.setAlignment(
            Qt.AlignCenter
        )

        self.preview_label.setMinimumSize(
            700,
            520
        )

        self.preview_label.setStyleSheet(
            """
            border:1px solid gray;
            background:#f5f5f5;
            """
        )

        right.addWidget(
            self.preview_label
        )

        self.ai_scroll_area = QScrollArea()

        self.ai_scroll_area.setWidgetResizable(True)

        self.ai_scroll_area.setMinimumHeight(300)

        self.ai_scroll_area.setMaximumHeight(600)

        self.ai_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                background: #ffffff;
            }
            QScrollBar:vertical {
                width: 8px;
            }
        """)

        self.ai_container = QWidget()

        self.ai_container.setStyleSheet("""
            QWidget {
                background: #ffffff;
                padding: 5px;
            }
        """)

        self.ai_layout = QVBoxLayout(
            self.ai_container
        )

        self.ai_layout.setSpacing(8)

        self.ai_layout.setContentsMargins(15, 15, 15, 15)

        self.default_info_label = QLabel(
            "图片信息将在这里显示"
        )

        self.default_info_label.setWordWrap(True)

        self.default_info_label.setStyleSheet("""
            color: #666;
            font-size: 14px;
            padding: 10px;
        """)

        self.ai_layout.addWidget(
            self.default_info_label
        )

        self.ai_layout.addStretch()

        self.ai_scroll_area.setWidget(
            self.ai_container
        )

        right.addWidget(
            self.ai_scroll_area
        )

        button_layout = QHBoxLayout()

        self.btn_open = QPushButton(
            "📂 打开文件夹"
        )

        self.btn_ai = QPushButton(
            "🤖 AI分析"
        )

        self.btn_auto = QPushButton(
            "📁 自动分类"
        )

        self.btn_organize = QPushButton(
            "🤖 AI智能整理"
        )

        self.btn_super = QPushButton(
            "🖼 AI超分"
        )

        self.btn_video = QPushButton(
            "🎬 视频抽帧"
        )

        button_layout.addWidget(
            self.btn_open
        )

        button_layout.addWidget(
            self.btn_ai
        )

        button_layout.addWidget(
            self.btn_auto
        )

        button_layout.addWidget(
            self.btn_organize
        )

        button_layout.addWidget(
            self.btn_super
        )

        button_layout.addWidget(
            self.btn_video
        )

        right.addLayout(
            button_layout
        )

        self.setStatusBar(
            QStatusBar()
        )

        self.statusBar().showMessage(
            "程序启动完成"
        )

    def connect_signal(self):

        self.btn_open.clicked.connect(
            self.open_folder
        )

        self.btn_ai.clicked.connect(
            self.start_ai_analysis
        )

        self.btn_search.clicked.connect(
            self.search_images
        )

        self.image_list_widget.currentRowChanged.connect(
            self.show_preview
        )

        self.btn_auto.clicked.connect(
            self.auto_classify
        )

        self.btn_organize.clicked.connect(
            self.ai_organize
        )

        self.btn_super.clicked.connect(
            self.super_resolution
        )

        self.btn_video.clicked.connect(
            self.extract_video_frames
        )

    def open_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "选择照片文件夹"
        )

        if not folder:
            return

        try:
            # Stage 2B+: load_images_from_folder() internally scans via
            # core.storage.LocalPhotoLibrary and returns list[str] of
            # absolute paths (byte-identical to the legacy output).
            images = load_images_from_folder(
                folder
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "错误",
                f"扫描失败：{e}"
            )
            return

        self.image_list = images
        self.image_list_widget.clear()

        for path in images:
            item = QListWidgetItem(
                os.path.basename(path)
            )
            pix = QPixmap(path)
            if not pix.isNull():
                icon = QIcon(
                    pix.scaled(
                        110,
                        110,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                )
                item.setIcon(icon)
            self.image_list_widget.addItem(item)

        self.statusBar().showMessage(
            f"加载完成，共 {len(images)} 张图片"
        )

    def show_preview(self, row):

        if row < 0 or row >= len(self.image_list):
            return

        path = self.image_list[row]
        pix = QPixmap(path)

        if not pix.isNull():
            pix = pix.scaled(
                self.preview_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.preview_label.setPixmap(pix)

        try:
            size = os.path.getsize(path) / 1024 / 1024
            time = datetime.fromtimestamp(
                os.path.getmtime(path)
            )
            if self.default_info_label.isVisible():
                self.default_info_label.setText(
                    f"文件：{os.path.basename(path)}\n"
                    f"大小：{size:.2f} MB\n"
                    f"时间：{time}\n"
                    f"路径：{path}"
                )
        except Exception as e:
            if self.default_info_label.isVisible():
                self.default_info_label.setText(str(e))

    # ===== AI面板辅助方法 =====

    def clear_ai_panel(self):
        self.default_info_label.hide()
        while self.ai_layout.count() > 0:
            item = self.ai_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                if widget == self.default_info_label:
                    continue
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout):
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                self._clear_layout(child.layout())

    def create_section_header(self, text):
        header = QLabel(text)
        header.setStyleSheet("""
            font-size: 15px;
            font-weight: bold;
            color: #333;
            padding: 8px 0 4px 0;
        """)
        return header

    def get_progress_color(self, percentage):
        if percentage >= 90:
            return "#4CAF50"
        elif percentage >= 60:
            return "#2196F3"
        elif percentage >= 30:
            return "#FF9800"
        else:
            return "#F44336"

    def create_classification_item(self, name, percentage):
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        name_label = QLabel(name)
        name_label.setStyleSheet("""
            font-size: 13px;
            color: #333;
            font-weight: 500;
        """)

        percent_label = QLabel(f"{percentage:.0f}%")
        percent_label.setStyleSheet(f"""
            font-size: 13px;
            color: {self.get_progress_color(percentage)};
            font-weight: bold;
        """)
        percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        top_row.addWidget(name_label)
        top_row.addWidget(percent_label)

        progress_bar = QProgressBar()
        progress_bar.setMinimum(0)
        progress_bar.setMaximum(100)
        progress_bar.setValue(int(percentage))
        progress_bar.setTextVisible(False)
        progress_bar.setFixedHeight(18)

        color = self.get_progress_color(percentage)
        progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #e0e0e0;
                border-radius: 9px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 9px;
            }}
        """)

        layout.addLayout(top_row)
        layout.addWidget(progress_bar)

        return container

    def create_separator(self):
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet("""
            QFrame {
                color: #e0e0e0;
                margin: 5px 0;
            }
        """)
        return separator

    def start_ai_analysis(self):

        if not self.image_list:
            QMessageBox.information(
                self,
                "提示",
                "请先打开图片文件夹"
            )
            return

        row = self.image_list_widget.currentRow()

        if row < 0:
            QMessageBox.information(
                self,
                "提示",
                "请选择一张图片"
            )
            return

        image_path = self.image_list[row]
        self.current_image_path = image_path

        try:
            if self.classifier is None:
                self.classifier = AIClassifier()

            result = self.classifier.analyze(image_path)

            self.clear_ai_panel()

            category_en = result.get("category", "未知")
            category_cn = LABEL_MAP.get(category_en, category_en)

            quality = result.get("quality", 0) * 100

            scores = result.get("scores", {})

            advice = self.advisor.generate_ai_advice(category_en, category_cn, quality, scores, image_path)

            title = QLabel("🤖 AI分析结果")
            title.setStyleSheet("""
                font-size: 18px;
                font-weight: bold;
                color: #1a1a1a;
                padding: 5px 0;
            """)
            self.ai_layout.addWidget(title)

            self.ai_layout.addWidget(
                self.create_section_header("📂 分类")
            )

            l1 = result.get("layer1", {})
            l2 = result.get("layer2", {})
            l3 = result.get("layer3", {})

            l1_cn = l1.get("label_cn", "") if l1 else ""
            l2_cn = l2.get("label_cn", "") if l2 else ""
            l3_cn = l3.get("label_cn", "") if l3 else ""

            if l1_cn:
                l1_label = QLabel(f"主体：{l1_cn}")
                l1_label.setStyleSheet("font-size: 15px; color: #555; padding: 2px 0;")
                self.ai_layout.addWidget(l1_label)

            final_display = advice["category_cn"]
            category_display = QLabel(f"物种：{final_display}")
            category_display.setStyleSheet("""
                font-size: 20px;
                font-weight: bold;
                color: #0078D4;
                padding: 4px 0;
            """)
            self.ai_layout.addWidget(category_display)

            if l3_cn:
                l3_label = QLabel(f"照片类型：{l3_cn}")
                l3_label.setStyleSheet("font-size: 14px; color: #888; padding: 2px 0;")
                self.ai_layout.addWidget(l3_label)

            self.ai_layout.addWidget(
                self.create_section_header("⭐ AI可信度")
            )

            quality_container = QWidget()
            quality_container.setStyleSheet("background: transparent;")
            quality_layout = QHBoxLayout(quality_container)
            quality_layout.setContentsMargins(0, 5, 0, 5)
            quality_layout.setSpacing(10)

            quality_bar = QProgressBar()
            quality_bar.setMinimum(0)
            quality_bar.setMaximum(100)
            quality_bar.setValue(int(quality))
            quality_bar.setTextVisible(False)
            quality_bar.setFixedHeight(24)

            q_color = self.get_progress_color(quality)
            quality_bar.setStyleSheet(f"""
                QProgressBar {{
                    background-color: #e0e0e0;
                    border-radius: 12px;
                    border: none;
                }}
                QProgressBar::chunk {{
                    background-color: {q_color};
                    border-radius: 12px;
                }}
            """)

            quality_percent = QLabel(f"AI置信度：{quality:.0f}%")
            quality_percent.setStyleSheet(f"""
                font-size: 18px;
                font-weight: bold;
                color: {q_color};
            """)

            quality_layout.addWidget(quality_bar, 1)
            quality_layout.addWidget(quality_percent)
            self.ai_layout.addWidget(quality_container)

            self.ai_layout.addWidget(self.create_separator())

            self.ai_layout.addWidget(
                self.create_section_header("📊 分类概率")
            )

            if scores:
                sorted_scores = sorted(
                    scores.items(),
                    key=lambda x: x[1],
                    reverse=True
                )

                for name, score in sorted_scores:
                    chinese_name = LABEL_MAP.get(name, name)
                    percentage = score * 100
                    item_widget = self.create_classification_item(
                        chinese_name,
                        percentage
                    )
                    self.ai_layout.addWidget(item_widget)
            else:
                no_data = QLabel("暂无详细概率数据")
                no_data.setStyleSheet("color: #999; font-size: 12px;")
                self.ai_layout.addWidget(no_data)

            self.ai_layout.addWidget(self.create_separator())

            self.ai_layout.addWidget(
                self.create_section_header("💡 AI建议")
            )

            self.current_ai_category = advice["category_cn"]

            detection_text = f"检测结果：\n{advice['detection']}"
            detection_label = QLabel(detection_text)
            detection_label.setWordWrap(True)
            detection_label.setStyleSheet("""
                font-size: 13px;
                color: #555;
                padding: 5px;
                background: #f9f9f9;
                border-left: 3px solid #0078D4;
                border-radius: 4px;
            """)
            self.ai_layout.addWidget(detection_label)

            suggestion_text = advice['suggestion']
            suggestion_label = QLabel(suggestion_text)
            suggestion_label.setWordWrap(True)
            suggestion_label.setStyleSheet("""
                font-size: 13px;
                color: #333;
                padding: 5px;
                margin-top: 5px;
            """)
            self.ai_layout.addWidget(suggestion_label)

            tags_text = "建议标签：\n" + "、".join(advice['tags'])
            tags_label = QLabel(tags_text)
            tags_label.setWordWrap(True)
            tags_label.setStyleSheet("""
                font-size: 13px;
                color: #555;
                padding: 5px;
                margin-top: 5px;
                background: #f0f8ff;
                border-radius: 4px;
            """)
            self.ai_layout.addWidget(tags_label)

            recommend_label = QLabel(f"推荐指数：\n{advice['stars']}")
            recommend_label.setStyleSheet("""
                font-size: 16px;
                color: #FFD700;
                font-weight: bold;
                padding: 5px;
            """)
            self.ai_layout.addWidget(recommend_label)

            self.ai_layout.addWidget(self.create_separator())

            self.ai_layout.addWidget(
                self.create_section_header("✍️ 人工反馈")
            )

            feedback_label = QLabel("如果AI判断有误，请选择正确分类：")
            feedback_label.setStyleSheet("""
                font-size: 13px;
                color: #555;
                padding: 5px 0;
            """)
            self.ai_layout.addWidget(feedback_label)

            human_categories = get_human_categories()

            self.feedback_combo = QComboBox()
            self.feedback_combo.addItems(human_categories)
            self.feedback_combo.setStyleSheet("""
                QComboBox {
                    font-size: 13px;
                    padding: 6px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                }
                QComboBox:hover {
                    border-color: #0078D4;
                }
            """)
            self.ai_layout.addWidget(self.feedback_combo)

            self.btn_submit_feedback = QPushButton("📤 提交反馈")
            self.btn_submit_feedback.setStyleSheet("""
                QPushButton {
                    font-size: 13px;
                    padding: 8px;
                    background: #0078D4;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #005a9e;
                }
            """)
            self.btn_submit_feedback.clicked.connect(self.submit_feedback)
            self.ai_layout.addWidget(self.btn_submit_feedback)

            self.ai_layout.addStretch()

            self.statusBar().showMessage("AI分析完成")

        except Exception as e:
            QMessageBox.critical(
                self,
                "AI分析失败",
                str(e)
            )

    def submit_feedback(self):
        if not self.current_image_path:
            QMessageBox.warning(self, "提示", "没有可反馈的图片")
            return

        human_category = self.feedback_combo.currentText()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            self.advisor.save_feedback(
                self.current_image_path,
                self.current_ai_category or "未知",
                human_category,
                timestamp,
            )

            QMessageBox.information(
                self,
                "反馈成功",
                f"已记录：AI判断为【{self.current_ai_category}】\n"
                f"人工标注为【{human_category}】\n\n"
                f"反馈已保存，正在刷新..."
            )

            self.start_ai_analysis()

        except Exception as e:
            QMessageBox.critical(
                self,
                "反馈失败",
                f"保存反馈时出错：{e}"
            )

    def search_images(self):

        keyword = self.search_edit.text().strip()

        self.image_list_widget.clear()

        if not keyword:
            result = self.image_list
        else:
            result = [
                p for p in self.image_list
                if keyword.lower()
                in os.path.basename(p).lower()
            ]

        for path in result:
            item = QListWidgetItem(
                os.path.basename(path)
            )
            pix = QPixmap(path)
            if not pix.isNull():
                icon = QIcon(
                    pix.scaled(
                        110,
                        110,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                )
                item.setIcon(icon)
            self.image_list_widget.addItem(item)

        self.statusBar().showMessage(
            f"搜索完成：{len(result)} 张图片"
        )

    def auto_classify(self):

        if not self.image_list:
            QMessageBox.information(
                self,
                "提示",
                "请先打开图片文件夹"
            )
            return

        target_folder = QFileDialog.getExistingDirectory(
            self,
            "选择归档目标文件夹"
        )

        if not target_folder:
            return

        reply = QMessageBox.question(
            self,
            "确认自动分类",
            f"将分析 {len(self.image_list)} 张图片，\n"
            f"按分类结果归档到：\n{target_folder}\n\n"
            f"自动跳过重复图片。\n"
            f"图片较多时可能需要较长时间。\n"
            f"是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        from core.auto_organizer import auto_organize

        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("自动分类进度")
        progress_dialog.setFixedSize(450, 180)
        progress_dialog.setModal(True)

        dialog_layout = QVBoxLayout(progress_dialog)

        self.progress_label = QLabel("准备开始...")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("""
            font-size: 14px;
            color: #333;
            padding: 15px;
        """)
        dialog_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                height: 24px;
                border-radius: 12px;
                background: #e0e0e0;
                border: none;
            }
            QProgressBar::chunk {
                background: #0078D4;
                border-radius: 12px;
            }
        """)
        dialog_layout.addWidget(self.progress_bar)

        progress_dialog.show()
        QApplication.processEvents()

        def on_progress(current, total, status_text):
            percent = int(current / total * 100) if total > 0 else 0
            self.progress_bar.setValue(percent)
            self.progress_label.setText(
                f"进度：{current}/{total}\n\n{status_text}"
            )
            QApplication.processEvents()

        try:
            stats = auto_organize(
                self.image_list,
                target_folder,
                mode="copy",
                remove_duplicates=True,
                progress_callback=on_progress
            )

            progress_dialog.close()

            report = f"自动分类完成！\n\n"
            report += f"✅ 成功：{stats['success']} 张\n"
            report += f"💾 缓存命中：{stats.get('cache_hits', 0)} 张\n"
            report += f"🔄 跳过重复：{stats.get('duplicates_skipped', 0)} 张\n"
            report += f"❌ 失败：{stats['failed']} 张\n\n"

            if stats["categories"]:
                report += "📊 分类统计：\n"
                for cat, count in sorted(
                    stats["categories"].items(),
                    key=lambda x: x[1],
                    reverse=True
                ):
                    report += f"  【{cat}】：{count} 张\n"

            if stats["errors"]:
                report += f"\n⚠️ 错误详情（前5条）：\n"
                for path, err in stats["errors"][:5]:
                    report += f"  {os.path.basename(path)}：{err}\n"

            QMessageBox.information(
                self,
                "自动分类完成",
                report
            )

            self.statusBar().showMessage(
                f"自动分类完成，成功 {stats['success']} 张，"
                f"缓存命中 {stats.get('cache_hits', 0)} 张，"
                f"跳过 {stats.get('duplicates_skipped', 0)} 张重复"
            )

        except Exception as e:
            progress_dialog.close()
            QMessageBox.critical(
                self,
                "自动分类失败",
                str(e)
            )

    def ai_organize(self):

        if not self.image_list:
            QMessageBox.information(
                self,
                "提示",
                "请先打开图片文件夹"
            )
            return

        reply = QMessageBox.question(
            self,
            "确认AI智能整理",
            f"将对 {len(self.image_list)} 张图片进行完整AI扫描：\n\n"
            f"1. 三级AI分类（主体/物种/类型）\n"
            f"2. 人物识别与聚合\n"
            f"3. 保存分析结果\n\n"
            f"可能需要较长时间，是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        from core.ai_organizer import AIOrganizer

        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("AI智能整理进度")
        progress_dialog.setFixedSize(450, 180)
        progress_dialog.setModal(True)

        dialog_layout = QVBoxLayout(progress_dialog)

        self.progress_label = QLabel("准备开始...")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("""
            font-size: 14px;
            color: #333;
            padding: 15px;
        """)
        dialog_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                height: 24px;
                border-radius: 12px;
                background: #e0e0e0;
                border: none;
            }
            QProgressBar::chunk {
                background: #0078D4;
                border-radius: 12px;
            }
        """)
        dialog_layout.addWidget(self.progress_bar)

        progress_dialog.show()
        QApplication.processEvents()

        self._organizer = AIOrganizer()

        def on_progress(step, message, percent):
            self.progress_bar.setValue(percent)
            self.progress_label.setText(message)
            QApplication.processEvents()

        try:
            result = self._organizer.organize_folder(
                self.image_list,
                progress_callback=on_progress
            )

            if result is None:
                raise RuntimeError("AIOrganizer 返回了空结果")

            progress_dialog.close()

            report = f"AI智能整理完成！\n\n"
            report += f"📊 分类统计：\n"
            for cat, count in result.get("categories", {}).items():
                report += f"  【{cat}】：{count} 张\n"

            characters = result.get("characters", [])
            if characters:
                real_count = sum(1 for c in characters if c.get("type") == "real_person")
                fursuit_count = sum(1 for c in characters if c.get("type") == "fursuit_character")
                report += f"\n👤 人物分组：{len(characters)} 组\n"
                report += f"  真人分组：{real_count} 组\n"
                report += f"  兽装角色分组：{fursuit_count} 组\n"

            QMessageBox.information(
                self,
                "AI智能整理完成",
                report
            )

            self.statusBar().showMessage(
                f"AI智能整理完成，{result['total']} 张图片，{len(characters)} 个人物分组"
            )

        except Exception as e:
            progress_dialog.close()
            QMessageBox.critical(
                self,
                "AI智能整理失败",
                str(e)
            )

    def super_resolution(self):

        QMessageBox.information(
            self,
            "AI超分",
            "AI超分功能正在开发中"
        )

    def extract_video_frames(self):

        QMessageBox.information(
            self,
            "视频抽帧",
            "视频抽帧功能正在开发中"
        )


if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = MainWindow()

    window.show()

    sys.exit(
        app.exec()
    )