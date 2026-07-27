"""
IW Solo PIXI — Log Uploader App  v1.0
Upload IW611 production test logs directly to PostgreSQL.
Dark-mode desktop UI, designed to match and exceed the reference uploader's
performance and UX.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from PyQt5.QtCore import Qt, QThread, QTimer, QSettings, pyqtSignal
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QDialog,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "PyQt5 is required.  Run: pip install PyQt5"
    ) from exc

try:
    import psycopg2
except ImportError as exc:
    raise SystemExit(
        "psycopg2 is required.  Run: pip install psycopg2-binary"
    ) from exc

# ---------------------------------------------------------------------------
# Path bootstrap — makes parsers/ingestion importable whether we're run from
# the project root OR from anywhere via an absolute path.
# ---------------------------------------------------------------------------
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from ingestion.postgres import PostgresRunRepository
from ingestion.service import collect_log_files, ingest_paths

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_VERSION = "1.0.0"
DB_CONNECT_TIMEOUT = 3
DB_HEARTBEAT_MS = 15_000      # 15 s — matches reference uploader
DEFAULT_DB_URL = "postgresql://pixi:pixipass@localhost:5434/pixi_test"
COMMIT_BATCH = 10             # kept in sync with ingestion.service._COMMIT_EVERY

# ---------------------------------------------------------------------------
# Dark-mode stylesheet (Apple-inspired, per project decision #13)
# ---------------------------------------------------------------------------
DARK_STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background-color: #1a1a1f;
}
QWidget {
    font-family: "Segoe UI Variable", "Segoe UI", "SF Pro Display", sans-serif;
    color: #e8e8ed;
    font-size: 13px;
}

QGroupBox {
    background-color: transparent;
    border: 1px solid #2c2c35;
    border-radius: 10px;
    margin-top: 18px;
    padding: 14px 12px 12px 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    color: #0a84ff;
    font-size: 13px;
    font-weight: 700;
}

QLineEdit {
    background-color: #2c2c35;
    border: 1px solid #3a3a45;
    border-radius: 7px;
    padding: 6px 10px;
    color: #e8e8ed;
    min-height: 24px;
}
QLineEdit:focus {
    border: 1.5px solid #0a84ff;
}
QLineEdit[readOnly="true"] {
    background-color: #232328;
    color: #8e8e93;
}

QPushButton {
    background-color: #0a84ff;
    color: white;
    border: none;
    border-radius: 7px;
    padding: 6px 16px;
    font-weight: 600;
    min-height: 26px;
}
QPushButton:hover  { background-color: #0070d8; }
QPushButton:pressed { background-color: #005cbf; }
QPushButton:disabled {
    background-color: #2c2c35;
    color: #636366;
}

QPushButton#secondary {
    background-color: #2c2c35;
    color: #e8e8ed;
    border: 1px solid #3a3a45;
}
QPushButton#secondary:hover  { background-color: #3a3a45; }
QPushButton#secondary:pressed { background-color: #48484e; }

QPushButton#danger {
    background-color: #ff453a;
    color: white;
}
QPushButton#danger:hover   { background-color: #d93025; }
QPushButton#danger:disabled {
    background-color: #2c2c35;
    color: #636366;
}

QListWidget {
    background-color: #2c2c35;
    border: 1px solid #3a3a45;
    border-radius: 7px;
    padding: 4px;
    font-family: Consolas, "Cascadia Code", monospace;
    font-size: 12px;
    outline: none;
}
QListWidget::item { padding: 5px 10px; border-radius: 4px; }
QListWidget::item:selected { background-color: #0a84ff; color: white; }
QListWidget::item:hover    { background-color: #3a3a45; }

QProgressBar {
    border: none;
    border-radius: 3px;
    background-color: #2c2c35;
    text-align: center;
    color: transparent;
    max-height: 6px;
    min-height: 6px;
}
QProgressBar::chunk { background-color: #30d158; border-radius: 3px; }

QLabel { background: transparent; }

QFrame#separator {
    background-color: #2c2c35;
    max-height: 1px;
}

QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1.5px solid #636366;
    border-radius: 4px;
    background: #2c2c35;
}
QCheckBox::indicator:checked {
    background-color: #ff453a;
    border-color: #ff453a;
}
"""


# ---------------------------------------------------------------------------
# DB Config Dialog
# ---------------------------------------------------------------------------
class DBConfigDialog(QDialog):
    def __init__(self, parent=None, settings: QSettings | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Database Configuration")
        self.setFixedSize(420, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(1, 1)

        self.inp_host = QLineEdit(self.settings.value("db_host", "localhost"))
        self.inp_port = QLineEdit(self.settings.value("db_port", "5434"))
        self.inp_db   = QLineEdit(self.settings.value("db_name", "pixi_test"))
        self.inp_user = QLineEdit(self.settings.value("db_user", "pixi"))
        self.inp_pass = QLineEdit(self.settings.value("db_pass", "pixipass"))
        self.inp_pass.setEchoMode(QLineEdit.Password)

        for row, (label, widget) in enumerate(zip(
            ["Host:", "Port:", "Database:", "User:", "Password:"],
            [self.inp_host, self.inp_port, self.inp_db, self.inp_user, self.inp_pass],
        )):
            lbl = QLabel(label)
            lbl.setStyleSheet("font-weight: 600; color: #8e8e93;")
            grid.addWidget(lbl, row, 0, Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(widget, row, 1)

        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_test = QPushButton("Test Connection")
        btn_test.setObjectName("secondary")
        btn_test.clicked.connect(self._test)
        btn_save = QPushButton("Save && Close")
        btn_save.clicked.connect(self.accept)
        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    def _test(self):
        dsn = (
            f"postgresql://{self.inp_user.text()}:{self.inp_pass.text()}"
            f"@{self.inp_host.text()}:{self.inp_port.text()}/{self.inp_db.text()}"
        )
        try:
            conn = psycopg2.connect(dsn, connect_timeout=DB_CONNECT_TIMEOUT)
            conn.close()
            QMessageBox.information(self, "Success", "Connection successful!")
        except Exception as exc:
            QMessageBox.critical(self, "Failed", f"Connection failed:\n{exc}")

    def save_settings(self):
        self.settings.setValue("db_host", self.inp_host.text())
        self.settings.setValue("db_port", self.inp_port.text())
        self.settings.setValue("db_name", self.inp_db.text())
        self.settings.setValue("db_user", self.inp_user.text())
        self.settings.setValue("db_pass", self.inp_pass.text())


# ---------------------------------------------------------------------------
# Upload Worker Thread
# ---------------------------------------------------------------------------
class UploadWorker(QThread):
    """
    Background thread that parses + ingests log files.

    Signals
    -------
    progress(pct: int)          — 0-100 percent
    stats(dict)                 — live counts: queued/uploaded/skipped/failed
    log_line(str)               — one human-readable log message per file
    finished_signal(str)        — summary string on completion
    error(str)                  — fatal error string
    """
    progress       = pyqtSignal(int)
    stats          = pyqtSignal(dict)
    log_line       = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    error          = pyqtSignal(str)

    def __init__(self, dsn: str, file_paths: list[str],
                 work_order: str | None = None,
                 replace_mode: bool = False):
        super().__init__()
        self.dsn          = dsn
        self.file_paths   = file_paths
        self.work_order   = work_order or None
        self.replace_mode = replace_mode
        self._stop        = False

    def stop(self):
        self._stop = True

    def run(self):
        # --- DB connect ---------------------------------------------------
        try:
            conn = psycopg2.connect(self.dsn, connect_timeout=DB_CONNECT_TIMEOUT)
        except Exception as exc:
            self.error.emit(f"Database connection failed: {exc}")
            return

        self.log_line.emit("✓ Database connected")

        # --- Replace mode -------------------------------------------------
        if self.replace_mode:
            self.log_line.emit("⚠️  Replace mode: truncating test_results …")
            try:
                with conn.cursor() as cur:
                    cur.execute("TRUNCATE TABLE test_results RESTART IDENTITY CASCADE")
                conn.commit()
                self.log_line.emit("✓ Database cleared — uploading fresh batch")
            except Exception as exc:
                self.error.emit(f"TRUNCATE failed: {exc}")
                conn.close()
                return

        # --- Pre-load hash cache (one DB query for all 180 files) ---------
        repo = PostgresRunRepository(conn)
        known = repo.preload_hashes()
        self.log_line.emit(f"✓ Hash cache loaded ({known} existing records)")

        total = len(self.file_paths)
        st = {"queued": total, "uploaded": 0, "skipped": 0, "failed": 0}
        self.stats.emit(dict(st))

        uploads_since_commit = 0

        def _progress(completed: int, _total: int, item) -> None:
            if self._stop:
                return
            pct = int(completed / total * 100)
            self.progress.emit(pct)
            status = item.status
            icon = {"uploaded": "✓", "duplicate": "⏭", "rejected": "✗"}.get(status, "·")
            fname = Path(item.source_file).name
            result_tag = f" → {item.result}" if item.result else ""
            self.log_line.emit(
                f"  [{completed}/{total}] {icon} {fname}{result_tag} — {status}"
            )
            # Translate service status to reference-style stat keys
            if status == "uploaded":
                st["uploaded"] += 1
            elif status == "duplicate":
                st["skipped"] += 1
            elif status == "rejected":
                st["failed"] += 1
            self.stats.emit(dict(st))

        # --- Ingest -------------------------------------------------------
        # ingest_paths does parallel parsing + sequential DB writes + periodic commits
        try:
            report = ingest_paths(
                self.file_paths,
                repo,
                work_order_override=self.work_order,
                source="gui",
                progress_callback=_progress,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            conn.close()
            return
        finally:
            # Final commit for any remainder
            try:
                conn.commit()
            except Exception:
                pass

        try:
            conn.close()
        except Exception:
            pass

        summary = (
            f"Upload completed. Total {total} files — "
            f"uploaded: {report.uploaded}  "
            f"skipped: {report.duplicates}  "
            f"failed: {report.rejected}"
        )
        self.finished_signal.emit(summary)


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------
class LogUploaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker: UploadWorker | None = None
        self._heartbeat: QTimer | None = None
        self.settings = QSettings("TN", "IWLogUploaderApp")
        self._build_ui()
        self.setStyleSheet(DARK_STYLESHEET)
        # Auto-check DB 300 ms after window shows
        QTimer.singleShot(300, self._check_db)
        self._start_heartbeat()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.setWindowTitle(f"IW Solo PIXI — Log Uploader  (v{APP_VERSION})")
        self.setMinimumSize(1000, 860)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        # ── Title row ────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        lbl_title = QLabel("IW Solo PIXI — Log Uploader")
        lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #e8e8ed;")
        lbl_sub = QLabel("NXP IW611 Production Test • IQfact+ Log Ingestion")
        lbl_sub.setStyleSheet("font-size: 13px; color: #8e8e93; font-weight: 500;")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        title_row.addLayout(title_col)
        title_row.addStretch()

        # DB status badge
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #636366; font-size: 20px;")
        self.db_label = QLabel("Database —")
        self.db_label.setStyleSheet("color: #8e8e93; font-size: 13px; font-weight: 700;")
        self.btn_settings = QPushButton("⚙  Settings")
        self.btn_settings.setObjectName("secondary")
        self.btn_settings.clicked.connect(self._open_settings)

        badge = QHBoxLayout()
        badge.setSpacing(8)
        badge.addWidget(self.dot)
        badge.addWidget(self.db_label)
        badge.addSpacing(12)
        badge.addWidget(self.btn_settings)
        title_row.addLayout(badge)
        layout.addLayout(title_row)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Info banner
        banner = QFrame()
        banner.setStyleSheet(
            "QFrame { background-color: #1c2a3a; border: 1px solid #1f4068;"
            " border-radius: 8px; }"
        )
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(14, 10, 14, 10)
        lbl_fmt_title = QLabel("Log Format:")
        lbl_fmt_title.setStyleSheet("color: #0a84ff; font-weight: 700; white-space: nowrap;")
        lbl_fmt_desc = QLabel(
            "YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt  (IQfact+ IW611 logs)"
        )
        lbl_fmt_desc.setStyleSheet("color: #8e8e93; font-size: 12px;")
        bl.addWidget(lbl_fmt_title)
        bl.addWidget(lbl_fmt_desc, 1)
        layout.addWidget(banner)

        # ── File selection ────────────────────────────────────────────
        file_group = QGroupBox("Log Files")
        fg = QVBoxLayout(file_group)
        fg.setContentsMargins(14, 18, 14, 14)
        fg.setSpacing(10)

        folder_row = QHBoxLayout()
        lbl_folder = QLabel("Log Folder:")
        lbl_folder.setStyleSheet("font-weight: 600; color: #8e8e93;")
        self.inp_folder = QLineEdit()
        self.inp_folder.setReadOnly(True)
        self.inp_folder.setPlaceholderText("Select a folder containing .txt log files …")
        self.inp_folder.setMinimumHeight(36)
        self.btn_browse = QPushButton("Browse Folder")
        self.btn_browse.setObjectName("secondary")
        self.btn_browse.setMinimumHeight(36)
        self.btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(lbl_folder)
        folder_row.addWidget(self.inp_folder, 1)
        folder_row.addWidget(self.btn_browse)
        fg.addLayout(folder_row)

        list_btn_row = QHBoxLayout()
        self.btn_add_files = QPushButton("Add Files")
        self.btn_add_files.setObjectName("secondary")
        self.btn_add_files.setMinimumHeight(36)
        self.btn_add_files.clicked.connect(self._add_files)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("secondary")
        self.btn_clear.setMinimumHeight(36)
        self.btn_clear.clicked.connect(self._clear_files)
        self.lbl_count = QLabel("0 files selected")
        self.lbl_count.setStyleSheet("color: #8e8e93; font-weight: 600;")
        list_btn_row.addWidget(self.btn_add_files)
        list_btn_row.addWidget(self.btn_clear)
        list_btn_row.addStretch()
        list_btn_row.addWidget(self.lbl_count)
        fg.addLayout(list_btn_row)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.file_list.setTextElideMode(Qt.ElideNone)
        self.file_list.setWordWrap(False)
        self.file_list.setMinimumHeight(120)
        self.file_list.setMaximumHeight(200)
        fg.addWidget(self.file_list)
        layout.addWidget(file_group)

        # ── Upload ───────────────────────────────────────────────────
        up_group = QGroupBox("Upload")
        ug = QVBoxLayout(up_group)
        ug.setContentsMargins(14, 18, 14, 14)
        ug.setSpacing(14)

        # Work-order row
        wo_row = QHBoxLayout()
        lbl_wo = QLabel("Work Order:")
        lbl_wo.setStyleSheet("font-weight: 600; color: #8e8e93;")
        self.inp_wo = QLineEdit()
        self.inp_wo.setPlaceholderText(
            "Optional override — parent folder name is used when blank"
        )
        self.inp_wo.setMinimumHeight(36)
        wo_row.addWidget(lbl_wo)
        wo_row.addWidget(self.inp_wo, 1)
        ug.addLayout(wo_row)

        # Action buttons
        action_row = QHBoxLayout()
        self.btn_upload = QPushButton("Upload to DB")
        self.btn_upload.setMinimumHeight(38)
        self.btn_upload.setMinimumWidth(160)
        self.btn_upload.clicked.connect(self._start_upload)
        self.btn_upload.setEnabled(False)

        self.btn_stop = QPushButton("Cancel")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_upload)

        self.btn_clear_stats = QPushButton("Clear Stats")
        self.btn_clear_stats.setObjectName("secondary")
        self.btn_clear_stats.setMinimumHeight(38)
        self.btn_clear_stats.clicked.connect(self._clear_stats)

        action_row.addWidget(self.btn_upload)
        action_row.addWidget(self.btn_stop)
        action_row.addStretch()
        action_row.addWidget(self.btn_clear_stats)
        ug.addLayout(action_row)

        # Replace mode
        self.chk_replace = QCheckBox(
            "Replace Mode — DELETE ALL existing records before uploading (TRUNCATE)"
        )
        self.chk_replace.setStyleSheet("color: #ff453a; font-weight: 600;")
        ug.addWidget(self.chk_replace)

        # Progress bar
        self.prog_bar = QProgressBar()
        self.prog_bar.setValue(0)
        self.prog_bar.setTextVisible(False)
        ug.addWidget(self.prog_bar)

        # Stat cards
        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        self._cards: dict[str, QLabel] = {}
        for key, label, color in [
            ("queued",   "Queued",   "#98989d"),
            ("uploaded", "Uploaded", "#30d158"),
            ("skipped",  "Skipped",  "#ff9f0a"),
            ("failed",   "Failed",   "#ff453a"),
        ]:
            card, val_lbl = self._make_card(label, "0", color)
            stats_row.addWidget(card)
            self._cards[key] = val_lbl
        ug.addLayout(stats_row)

        # Status label
        self.lbl_status = QLabel("Upload status: idle")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMinimumHeight(24)
        self.lbl_status.setStyleSheet("color: #8e8e93; font-weight: 600;")
        ug.addWidget(self.lbl_status)

        # Log output
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(160)
        self.log_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.log_list.setTextElideMode(Qt.ElideNone)
        ug.addWidget(self.log_list)

        layout.addWidget(up_group)

    # ------------------------------------------------------------------
    # Stat card helper
    # ------------------------------------------------------------------
    @staticmethod
    def _make_card(label: str, value: str, color: str) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #232328; border: 1px solid #2c2c35;"
            " border-radius: 10px; }"
        )
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        frame.setFixedHeight(88)
        vl = QVBoxLayout(frame)
        vl.setContentsMargins(14, 10, 14, 10)
        vl.setSpacing(4)
        val_lbl = QLabel(value)
        val_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 26px; color: {color}; background: transparent;"
        )
        val_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        txt_lbl = QLabel(label)
        txt_lbl.setStyleSheet("font-size: 12px; color: #636366; font-weight: 600; background: transparent;")
        txt_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        vl.addWidget(val_lbl)
        vl.addWidget(txt_lbl)
        return frame, val_lbl

    # ------------------------------------------------------------------
    # DB status
    # ------------------------------------------------------------------
    def _build_dsn(self) -> str:
        host = self.settings.value("db_host", "localhost")
        port = self.settings.value("db_port", "5434")
        db   = self.settings.value("db_name", "pixi_test")
        user = self.settings.value("db_user", "pixi")
        pwd  = self.settings.value("db_pass", "pixipass")
        return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

    def _start_heartbeat(self):
        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._check_db)
        self._heartbeat.start(DB_HEARTBEAT_MS)

    def _check_db(self):
        dsn = self._build_dsn()
        try:
            conn = psycopg2.connect(dsn, connect_timeout=DB_CONNECT_TIMEOUT)
            conn.close()
            self.dot.setStyleSheet("color: #30d158; font-size: 20px;")
            self.db_label.setText("Database Online")
            self.db_label.setStyleSheet("color: #30d158; font-size: 13px; font-weight: 700;")
        except Exception:
            self.dot.setStyleSheet("color: #ff453a; font-size: 20px;")
            self.db_label.setText("Database Offline")
            self.db_label.setStyleSheet("color: #ff453a; font-size: 13px; font-weight: 700;")

    def _open_settings(self):
        dlg = DBConfigDialog(self, self.settings)
        dlg.setStyleSheet(DARK_STYLESHEET)
        if dlg.exec_():
            dlg.save_settings()
            self._check_db()

    # ------------------------------------------------------------------
    # File selection
    # ------------------------------------------------------------------
    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select IW611 Log Folder")
        if not folder:
            return
        self.inp_folder.setText(os.path.normpath(folder))
        paths = collect_log_files([folder])
        existing = {self.file_list.item(i).text() for i in range(self.file_list.count())}
        added = 0
        for p in sorted(paths):
            fp = str(p)
            if fp not in existing:
                self.file_list.addItem(fp)
                added += 1
        self._update_count()

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select IW611 Log Files", "", "Text Files (*.txt);;All Files (*)"
        )
        if not files:
            return
        paths = collect_log_files(files)
        existing = {self.file_list.item(i).text() for i in range(self.file_list.count())}
        for p in paths:
            fp = str(p)
            if fp not in existing:
                self.file_list.addItem(fp)
        self._update_count()

    def _clear_files(self):
        self.file_list.clear()
        self.inp_folder.clear()
        self._update_count()

    def _update_count(self):
        n = self.file_list.count()
        self.lbl_count.setText(f"{n} file{'s' if n != 1 else ''} selected")
        self.btn_upload.setEnabled(n > 0)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def _start_upload(self):
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "Warning", "Please select log files first.")
            return
        if self.worker and self.worker.isRunning():
            return

        replace = self.chk_replace.isChecked()
        if replace:
            reply = QMessageBox.warning(
                self,
                "Confirm Replace Mode",
                "Replace Mode will DELETE ALL existing records.\n\nThis cannot be undone. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        file_paths = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        wo = self.inp_wo.text().strip() or None

        self._set_uploading(True)
        self.prog_bar.setValue(0)
        self.log_list.clear()
        self.lbl_status.setText(f"Upload status: uploading {len(file_paths)} files …")
        self.lbl_status.setStyleSheet("color: #8e8e93; font-weight: 600;")

        # Reset stat cards
        for key in self._cards:
            self._cards[key].setText("0")
        self._cards["queued"].setText(str(len(file_paths)))

        self.worker = UploadWorker(self._build_dsn(), file_paths, wo, replace)
        self.worker.progress.connect(self.prog_bar.setValue)
        self.worker.stats.connect(self._on_stats)
        self.worker.log_line.connect(self._on_log)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _stop_upload(self):
        if self.worker:
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.lbl_status.setText("Upload status: cancellation requested …")
            self.lbl_status.setStyleSheet("color: #ff9f0a; font-weight: 600;")

    def _set_uploading(self, state: bool):
        enabled = not state
        self.btn_upload.setEnabled(False if state else self.file_list.count() > 0)
        self.btn_stop.setEnabled(state)
        self.btn_browse.setEnabled(enabled)
        self.btn_add_files.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)
        self.btn_settings.setEnabled(enabled)
        self.inp_wo.setEnabled(enabled)
        self.chk_replace.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Worker signals
    # ------------------------------------------------------------------
    def _on_stats(self, st: dict):
        for key, lbl in self._cards.items():
            lbl.setText(str(st.get(key, 0)))

    def _on_log(self, msg: str):
        self.log_list.addItem(msg)
        self.log_list.scrollToBottom()

    def _on_finished(self, summary: str):
        self.lbl_status.setText(f"Upload status: completed")
        self.lbl_status.setStyleSheet("color: #30d158; font-weight: 700;")
        self._set_uploading(False)
        self.prog_bar.setValue(100)
        QTimer.singleShot(500, self._check_db)
        QMessageBox.information(self, "Upload Complete", summary)

    def _on_error(self, msg: str):
        self.lbl_status.setText(f"Upload status: error — {msg}")
        self.lbl_status.setStyleSheet("color: #ff453a; font-weight: 700;")
        self._set_uploading(False)
        QMessageBox.critical(self, "Error", msg)

    def _clear_stats(self):
        self.prog_bar.setValue(0)
        for lbl in self._cards.values():
            lbl.setText("0")
        self.log_list.clear()
        self.lbl_status.setText("Upload status: idle")
        self.lbl_status.setStyleSheet("color: #8e8e93; font-weight: 600;")

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if self._heartbeat:
            self._heartbeat.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = LogUploaderApp()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
