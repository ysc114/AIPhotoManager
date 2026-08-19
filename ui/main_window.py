import os
from datetime import datetime

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QProgressBar
)


from core.image_loader import load_images_from_folder
from core.ai_classifier import AIClassifier



class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "AI Photo Manager V3"
        )

        self.resize(
            1200,
            800
        )


        # 图片路径
        self.image_list = []


        # AI模块
        try:
            self.ai = AIClassifier()
            print("AI模块加载成功")

        except Exception as e:
            print(
                "AI加载失败:",
                e
            )
            self.ai = None



        # 主窗口

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)



        self.title = QLabel(
            "AI Photo Manager"
        )

        root.addWidget(self.title)



        self.path_label = QLabel(
            "未选择文件夹"
        )

        root.addWidget(
            self.path_label
        )



        body = QHBoxLayout()

        root.addLayout(body)



        # 左边列表

        self.image_list_widget = QListWidget()

        self.image_list_widget.setIconSize(
            QSize(100,100)
        )

        body.addWidget(
            self.image_list_widget,
            1
        )



        # 右边

        right = QVBoxLayout()

        body.addLayout(
            right,
            2
        )



        self.preview_label = QLabel(
            "请选择图片"
        )

        self.preview_label.setAlignment(
            Qt.AlignCenter
        )

        self.preview_label.setMinimumSize(
            500,
            500
        )

        right.addWidget(
            self.preview_label
        )



        self.info_label = QLabel(
            "图片信息"
        )

        self.info_label.setWordWrap(
            True
        )

        right.addWidget(
            self.info_label
        )



        self.progress = QProgressBar()

        self.progress.hide()

        right.addWidget(
            self.progress
        )



        self.btn_open = QPushButton(
            "打开照片文件夹"
        )


        self.btn_ai = QPushButton(
            "开始 AI 分析"
        )


        root.addWidget(
            self.btn_open
        )

        root.addWidget(
            self.btn_ai
        )



        # 信号连接

        self.btn_open.clicked.connect(
            self.open_folder
        )

        self.btn_ai.clicked.connect(
            self.start_ai_analysis
        )

        self.image_list_widget.currentRowChanged.connect(
            self.show_preview
        )



    # 打开文件夹

    def open_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "选择照片文件夹"
        )


        if not folder:
            return


        self.image_list = load_images_from_folder(
            folder
        )


        self.path_label.setText(
            folder
        )


        self.image_list_widget.clear()


        for path in self.image_list:

            item = QListWidgetItem(
                os.path.basename(path)
            )

            item.setIcon(
                QIcon(path)
            )


            self.image_list_widget.addItem(
                item
            )


        QMessageBox.information(
            self,
            "完成",
            f"找到 {len(self.image_list)} 张图片"
        )



    # 图片预览

    def show_preview(self,row):

        if row < 0:
            return


        path = self.image_list[row]


        pix = QPixmap(path)


        if not pix.isNull():

            pix = pix.scaled(
                self.preview_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            self.preview_label.setPixmap(
                pix
            )


        size = os.path.getsize(path)/1024/1024

        t=datetime.fromtimestamp(
            os.path.getmtime(path)
        )


        self.info_label.setText(
            f"""
文件:
{os.path.basename(path)}

大小:
{size:.2f} MB

时间:
{t}

路径:
{path}
"""
        )



    # AI分析

    def start_ai_analysis(self):

        if not self.image_list:

            QMessageBox.warning(
                self,
                "提示",
                "请先打开图片文件夹"
            )

            return



        if self.ai is None:

            QMessageBox.warning(
                self,
                "错误",
                "AI模块没有加载"
            )

            return



        self.progress.show()

        self.progress.setMaximum(
            len(self.image_list)
        )


        for i,path in enumerate(self.image_list):

            print(
                "分析:",
                path
            )


            try:

                result = self.ai.analyze(
                    path
                )


                print(
                    result
                )


                item=self.image_list_widget.item(i)


                item.setText(
                    os.path.basename(path)
                    +
                    " | "
                    +
                    str(result)
                )


            except Exception as e:

                print(
                    "AI错误:",
                    e
                )


            self.progress.setValue(
                i+1
            )


        QMessageBox.information(
            self,
            "完成",
            "AI分析完成"
        )


        self.progress.hide()