"""PyQt5 GUI utility for splitting consolidated IW611 IQfact logs."""

from __future__ import annotations

import os
from pathlib import Path
import sys

try:
    from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import (
        QApplication,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSizePolicy,
        QStyle,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    if __name__ == "__main__":
        print(
            "無法啟動 GUI：尚未安裝 PyQt5。\n"
            "請先執行：python -m pip install PyQt5",
            file=sys.stderr,
        )
        raise SystemExit(1)
    raise

from iw_log_splitter_core import (
    SplitReport,
    create_summary_file,
    split_log_file,
    summarize_output_directory,
)


APP_STYLESHEET = """
QWidget {
    font-family: "Noto Sans TC", "Microsoft JhengHei UI", "Segoe UI", sans-serif;
    color: #2e3436;
    font-size: 12px;
}
QMainWindow {
    background: #f7f5f4;
}
QFrame#headerBar {
    background: #2c001e;
    border: 1px solid #4f233f;
    border-radius: 10px;
}
QLabel#windowTitle {
    color: #fff7f3;
    font-size: 18px;
    font-weight: 700;
}
QLabel#windowSubtitle {
    color: #ded1d9;
    font-size: 11px;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #d8d4cf;
    border-radius: 8px;
    margin-top: 18px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: #5e2750;
    font-size: 13px;
    font-weight: 600;
}
QLineEdit {
    min-height: 28px;
    padding: 2px 9px;
    background: #ffffff;
    border: 1px solid #c9c4be;
    border-radius: 5px;
    selection-background-color: #77216f;
}
QLineEdit:focus {
    border-color: #77216f;
}
QLineEdit:read-only {
    background: #f7f5f4;
}
QPushButton {
    min-height: 30px;
    padding: 3px 13px;
    color: #ffffff;
    background: #e95420;
    border: none;
    border-radius: 5px;
    font-weight: 600;
}
QPushButton:hover {
    background: #d84d1e;
}
QPushButton:pressed {
    background: #c8471b;
}
QPushButton:disabled {
    color: #fff8f5;
    background: #efb7a2;
}
QPushButton#secondary {
    color: #2e3436;
    background: #f5f3f1;
    border: 1px solid #c9c4be;
}
QPushButton#secondary:hover {
    background: #e9e5e1;
}
QPushButton#summary {
    background: #77216f;
}
QPushButton#summary:hover {
    background: #651b5d;
}
QFrame#statCard {
    background: #fffefe;
    border: 1px solid #dfd8d2;
    border-radius: 8px;
}
QFrame#statCardAccent {
    background: #fff7f4;
    border: 1px solid #efc3b0;
    border-radius: 8px;
}
QLabel#statLabel {
    color: #6b5f68;
    font-size: 11px;
    font-weight: 600;
}
QLabel#statValue {
    color: #2e3436;
    font-size: 20px;
    font-weight: 700;
}
QLabel#statHint {
    color: #7b727a;
    font-size: 10px;
}
QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #d8d4cf;
    border-radius: 7px;
    padding: 7px;
    font-family: Consolas, "Noto Sans Mono CJK TC", monospace;
}
QProgressBar {
    min-height: 17px;
    border: 1px solid #c9c4be;
    border-radius: 4px;
    background: #ffffff;
    text-align: center;
}
QProgressBar::chunk {
    background: #e95420;
    border-radius: 3px;
}
"""


def get_base_dir() -> Path:
    bundled_dir = getattr(sys, "_MEIPASS", None)
    return Path(bundled_dir) if bundled_dir else Path(__file__).resolve().parent


def find_app_icon() -> Path | None:
    candidates = (
        get_base_dir() / "build_assets" / "icons" / "solo_pixi_splitter.ico",
        get_base_dir()
        / "Ref_Project"
        / "build_assets"
        / "icons"
        / "solo_pixi_splitter.ico",
    )
    return next((path for path in candidates if path.is_file()), None)


class SplitWorker(QThread):
    progress = pyqtSignal(str)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, source_path: str, output_dir: str) -> None:
        super().__init__()
        self.source_path = source_path
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            report = split_log_file(
                self.source_path,
                self.output_dir,
                progress=self.progress.emit,
            )
            self.completed.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc))


class SummaryWorker(QThread):
    progress = pyqtSignal(str)
    completed = pyqtSignal(object, str)
    failed = pyqtSignal(str)

    def __init__(self, output_dir: str) -> None:
        super().__init__()
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            self.progress.emit(f"掃描輸出資料夾：{self.output_dir}")
            report = summarize_output_directory(self.output_dir)
            summary_path = create_summary_file(self.output_dir, report)
            self.completed.emit(report, str(summary_path))
        except Exception as exc:
            self.failed.emit(str(exc))


class IWLogSplitterWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.split_worker: SplitWorker | None = None
        self.summary_worker: SummaryWorker | None = None
        self.output_was_selected = False
        self.worker_failed = False
        self.stat_values: dict[str, QLabel] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("IW611 Log Splitter")
        self.setMinimumSize(920, 760)
        self.resize(980, 820)
        self.setStyleSheet(APP_STYLESHEET)

        icon_path = find_app_icon()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(self._create_header())
        layout.addWidget(self._create_paths_group())
        layout.addWidget(self._create_rules_group())
        layout.addWidget(self._create_stats_group())

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("等待執行")
        self.progress_bar.setAccessibleName("分切進度")
        layout.addWidget(self.progress_bar)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlaceholderText("分切進度、略過原因及摘要狀態會顯示在這裡。")
        self.console.setAccessibleName("處理訊息")
        self.console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.console, 1)

    def _create_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("headerBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(17, 14, 17, 14)
        layout.setSpacing(10)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title = QLabel("IW611 Log Splitter")
        title.setObjectName("windowTitle")
        subtitle = QLabel(
            "每個 IQfact Timestamp Run 各自輸出，依最終摘要判定 PASS／FAIL／STOP"
        )
        subtitle.setObjectName("windowSubtitle")
        subtitle.setWordWrap(True)
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        layout.addLayout(title_layout, 1)

        self.start_button = QPushButton("開始分切")
        self.start_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.start_button.setIconSize(QSize(17, 17))
        self.start_button.setMinimumHeight(40)
        self.start_button.setAccessibleName("開始分切")
        self.start_button.clicked.connect(self.start_split)
        layout.addWidget(self.start_button)

        self.summary_button = QPushButton("產生摘要")
        self.summary_button.setObjectName("summary")
        self.summary_button.setIcon(
            self.style().standardIcon(QStyle.SP_FileDialogDetailedView)
        )
        self.summary_button.setIconSize(QSize(17, 17))
        self.summary_button.setMinimumHeight(40)
        self.summary_button.setAccessibleName("產生輸出摘要")
        self.summary_button.clicked.connect(self.start_summary)
        layout.addWidget(self.summary_button)
        return header

    def _create_paths_group(self) -> QGroupBox:
        group = QGroupBox("來源與輸出")
        form = QFormLayout(group)
        form.setContentsMargins(15, 18, 15, 13)
        form.setSpacing(9)

        self.source_input = QLineEdit()
        self.source_input.setReadOnly(True)
        self.source_input.setPlaceholderText("請選擇 Log_all.txt")
        self.source_input.setAccessibleName("來源 Log_all 檔案")
        browse_source = QPushButton("選擇檔案")
        browse_source.setObjectName("secondary")
        browse_source.setIcon(
            self.style().standardIcon(QStyle.SP_DialogOpenButton)
        )
        browse_source.clicked.connect(self.browse_source)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_input, 1)
        source_row.addWidget(browse_source)

        self.output_input = QLineEdit(str(get_base_dir() / "split_output"))
        self.output_input.setPlaceholderText("請選擇輸出資料夾")
        self.output_input.setAccessibleName("輸出資料夾")
        browse_output = QPushButton("選擇資料夾")
        browse_output.setObjectName("secondary")
        browse_output.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        browse_output.clicked.connect(self.browse_output)

        output_row = QHBoxLayout()
        output_row.addWidget(self.output_input, 1)
        output_row.addWidget(browse_output)

        form.addRow("來源檔案：", source_row)
        form.addRow("輸出資料夾：", output_row)
        return group

    def _create_rules_group(self) -> QGroupBox:
        group = QGroupBox("已套用規則")
        layout = QGridLayout(group)
        layout.setContentsMargins(15, 18, 15, 13)
        layout.setHorizontalSpacing(22)
        layout.setVerticalSpacing(6)

        rules = (
            ("分切邊界", "每個 Timestamp Run"),
            ("檔名", "YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt"),
            ("MAC1", "Generetaed MAC Address／MAC_ADDRESS"),
            ("MAC2", "BD_ADDRESS"),
            ("結果", "最終摘要；無結論時為 STOP"),
            ("同名處理", "保留既有檔案並加 _1、_2…"),
        )
        for index, (label_text, value_text) in enumerate(rules):
            row, column = divmod(index, 2)
            rule = QLabel(f"<b>{label_text}：</b>{value_text}")
            rule.setWordWrap(True)
            layout.addWidget(rule, row, column)
        return group

    def _create_stats_group(self) -> QGroupBox:
        group = QGroupBox("處理統計")
        layout = QGridLayout(group)
        layout.setContentsMargins(15, 18, 15, 13)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        cards = (
            ("total", "Timestamp Runs", "偵測總數", True),
            ("created", "輸出檔案", "本次建立", False),
            ("skipped", "略過", "無法處理", False),
            ("pass", "PASS", "最終通過", False),
            ("fail", "FAIL", "最終失敗", False),
            ("stop", "STOP", "未完成", False),
            ("pass_rate", "Pass Rate", "PASS／總數", True),
            ("fail_rate", "Fail Rate", "FAIL／總數", False),
        )
        for index, (key, title, hint, accent) in enumerate(cards):
            row, column = divmod(index, 4)
            layout.addWidget(
                self._create_stat_card(key, title, hint, accent),
                row,
                column,
            )
        return group

    def _create_stat_card(
        self,
        key: str,
        title: str,
        hint: str,
        accent: bool,
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("statCardAccent" if accent else "statCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setObjectName("statLabel")
        value_label = QLabel("0.00%" if key.endswith("_rate") else "0")
        value_label.setObjectName("statValue")
        hint_label = QLabel(hint)
        hint_label.setObjectName("statHint")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(hint_label)
        self.stat_values[key] = value_label
        return frame

    def browse_source(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "選擇 IW611 Log_all.txt",
            self.source_input.text() or str(Path.home()),
            "Text logs (*.txt);;All files (*.*)",
        )
        if not selected:
            return
        self.source_input.setText(selected)
        if not self.output_was_selected:
            self.output_input.setText(str(Path(selected).parent / "split_output"))
        self.append_message(f"來源檔案：{selected}")

    def browse_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "選擇輸出資料夾",
            self.output_input.text() or str(Path.home()),
        )
        if selected:
            self.output_was_selected = True
            self.output_input.setText(selected)
            self.append_message(f"輸出資料夾：{selected}")

    def start_split(self) -> None:
        source = self.source_input.text().strip()
        output = self.output_input.text().strip()
        if not source or not Path(source).is_file():
            QMessageBox.warning(self, "來源檔案無效", "請選擇存在的 Log_all.txt。")
            return
        if not output:
            QMessageBox.warning(self, "輸出資料夾無效", "請指定輸出資料夾。")
            return

        self.console.clear()
        self.worker_failed = False
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("處理中…")
        self.set_busy(True)
        self.split_worker = SplitWorker(source, output)
        self.split_worker.progress.connect(self.append_message)
        self.split_worker.completed.connect(self.on_split_completed)
        self.split_worker.failed.connect(self.on_worker_failed)
        self.split_worker.finished.connect(self.on_worker_finished)
        self.split_worker.start()

    def start_summary(self) -> None:
        output = self.output_input.text().strip()
        if not output or not Path(output).is_dir():
            QMessageBox.warning(self, "輸出資料夾無效", "請選擇已存在的輸出資料夾。")
            return

        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("建立摘要中…")
        self.worker_failed = False
        self.set_busy(True)
        self.summary_worker = SummaryWorker(output)
        self.summary_worker.progress.connect(self.append_message)
        self.summary_worker.completed.connect(self.on_summary_completed)
        self.summary_worker.failed.connect(self.on_worker_failed)
        self.summary_worker.finished.connect(self.on_worker_finished)
        self.summary_worker.start()

    def on_split_completed(self, report: SplitReport) -> None:
        self.update_stats(report)
        self.append_message(
            "完成："
            f"建立 {report.created}，PASS {report.pass_count}，"
            f"FAIL {report.fail_count}，STOP {report.stop_count}。"
        )
        QMessageBox.information(
            self,
            "分切完成",
            f"已建立 {report.created} 個檔案。\n"
            f"PASS：{report.pass_count}\n"
            f"FAIL：{report.fail_count}\n"
            f"STOP：{report.stop_count}\n"
            f"略過：{report.skipped}",
        )

    def on_summary_completed(self, report: SplitReport, summary_path: str) -> None:
        self.update_stats(report)
        self.append_message(f"摘要已建立：{summary_path}")
        QMessageBox.information(self, "摘要完成", f"摘要檔案：\n{summary_path}")

    def on_worker_failed(self, error_message: str) -> None:
        self.worker_failed = True
        self.append_message(f"錯誤：{error_message}")
        QMessageBox.critical(self, "處理失敗", error_message)

    def on_worker_finished(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0 if self.worker_failed else 100)
        self.progress_bar.setFormat("作業失敗" if self.worker_failed else "作業完成")
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        self.start_button.setDisabled(busy)
        self.summary_button.setDisabled(busy)

    def append_message(self, message: str) -> None:
        self.console.appendPlainText(message)
        self.console.verticalScrollBar().setValue(
            self.console.verticalScrollBar().maximum()
        )

    def update_stats(self, report: SplitReport) -> None:
        values = {
            "total": str(report.total),
            "created": str(report.created),
            "skipped": str(report.skipped),
            "pass": str(report.pass_count),
            "fail": str(report.fail_count),
            "stop": str(report.stop_count),
            "pass_rate": f"{report.pass_rate:.2f}%",
            "fail_rate": f"{report.fail_rate:.2f}%",
        }
        for key, value in values.items():
            self.stat_values[key].setText(value)

    def closeEvent(self, event) -> None:
        workers = (self.split_worker, self.summary_worker)
        if any(worker and worker.isRunning() for worker in workers):
            QMessageBox.warning(self, "作業進行中", "請等待目前作業完成後再關閉。")
            event.ignore()
            return
        event.accept()


def main() -> int:
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("IW611 Log Splitter")
    window = IWLogSplitterWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
