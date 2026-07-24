from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from PyQt5.QtCore import Qt, QThread, pyqtSignal
    from PyQt5.QtWidgets import (
        QApplication,
        QFileDialog,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit("PyQt5 is required for the GUI uploader. Install PyQt5, then run this file again.") from exc

import psycopg2

from api.db import init_schema
from ingestion.postgres import PostgresRunRepository
from ingestion.service import collect_log_files, ingest_paths


DEFAULT_DB_URL = "postgresql://pixi:pixipass@localhost:5434/pixi_test"


class UploadWorker(QThread):
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, paths, db_url, work_order):
        super().__init__()
        self.paths = paths
        self.db_url = db_url
        self.work_order = work_order or None

    def run(self):
        old_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = self.db_url
        try:
            init_schema()
            conn = psycopg2.connect(self.db_url)
            try:
                report = ingest_paths(self.paths, PostgresRunRepository(conn), self.work_order, source="gui")
                conn.commit()
                self.finished.emit(report.as_dict())
            finally:
                conn.close()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if old_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old_url


class UploaderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IW Solo PIXI Log Uploader")
        self.resize(980, 650)
        self.paths = []
        self.worker = None
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)

        db_group = QGroupBox("Database")
        db_layout = QGridLayout(db_group)
        self.db_url = QLineEdit(DEFAULT_DB_URL)
        self.work_order = QLineEdit()
        self.work_order.setPlaceholderText("Optional override; folder name is used when blank")
        test_button = QPushButton("Test Connection")
        test_button.clicked.connect(self.test_connection)
        db_layout.addWidget(QLabel("URL"), 0, 0)
        db_layout.addWidget(self.db_url, 0, 1)
        db_layout.addWidget(test_button, 0, 2)
        db_layout.addWidget(QLabel("Work Order"), 1, 0)
        db_layout.addWidget(self.work_order, 1, 1, 1, 2)
        layout.addWidget(db_group)

        actions = QHBoxLayout()
        browse_button = QPushButton("Browse Folder")
        browse_button.clicked.connect(self.browse_folder)
        upload_button = QPushButton("Upload")
        upload_button.clicked.connect(self.upload)
        actions.addWidget(browse_button)
        actions.addWidget(upload_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.summary = QLabel("No folder selected")
        layout.addWidget(self.summary)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File", "Result", "MAC1", "Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.setCentralWidget(root)

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget { font-family: Segoe UI; font-size: 10pt; background: #11141a; color: #eef2f8; }
            QGroupBox { border: 1px solid #303846; border-radius: 8px; margin-top: 12px; padding: 12px; }
            QLineEdit, QTableWidget { border: 1px solid #303846; border-radius: 6px; background: #181d25; color: #eef2f8; padding: 6px; }
            QPushButton { border: 1px solid #303846; border-radius: 6px; background: #232b37; color: #eef2f8; padding: 8px 12px; }
            QPushButton:hover { background: #2d3746; }
            QProgressBar { border: 1px solid #303846; border-radius: 6px; text-align: center; }
            QProgressBar::chunk { background: #50c8b8; border-radius: 6px; }
            """
        )

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select IW611 log folder")
        if not folder:
            return
        self.paths = collect_log_files([folder])
        if not self.work_order.text().strip():
            self.work_order.setText(Path(folder).name)
        self.summary.setText(f"{len(self.paths)} IW log files selected")
        self.progress.setRange(0, max(1, len(self.paths)))
        self.progress.setValue(0)
        self._populate_preview()

    def _populate_preview(self):
        self.table.setRowCount(len(self.paths))
        for row, path in enumerate(self.paths):
            parts = path.stem.split("_")
            self.table.setItem(row, 0, QTableWidgetItem(path.name))
            self.table.setItem(row, 1, QTableWidgetItem(parts[-1] if parts else ""))
            self.table.setItem(row, 2, QTableWidgetItem(parts[2] if len(parts) > 2 else ""))
            self.table.setItem(row, 3, QTableWidgetItem("ready"))

    def test_connection(self):
        try:
            conn = psycopg2.connect(self.db_url.text().strip(), connect_timeout=5)
            conn.close()
            QMessageBox.information(self, "Connection", "Database connection succeeded.")
        except Exception as exc:
            QMessageBox.critical(self, "Connection", str(exc))

    def upload(self):
        if not self.paths:
            QMessageBox.warning(self, "Upload", "Select a folder first.")
            return
        self.progress.setRange(0, 0)
        self.worker = UploadWorker(self.paths, self.db_url.text().strip(), self.work_order.text().strip())
        self.worker.finished.connect(self.upload_finished)
        self.worker.failed.connect(self.upload_failed)
        self.worker.start()

    def upload_finished(self, report):
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.summary.setText(
            f"Uploaded {report['uploaded']}, duplicates {report['duplicates']}, rejected {report['rejected']}, warnings {report['warnings']}"
        )
        for item in report["files"]:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == Path(item["source_file"]).name:
                    self.table.setItem(row, 3, QTableWidgetItem(item["status"]))
                    break

    def upload_failed(self, message):
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        QMessageBox.critical(self, "Upload failed", message)


def main() -> int:
    app = QApplication(sys.argv)
    window = UploaderWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
