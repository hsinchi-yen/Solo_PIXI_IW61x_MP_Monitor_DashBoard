"""IW611 splitter core for the desktop splitter app.

The implementation lives in iw-solo-pixi-essential/ingestion/splitter.py so the web Log
Uploader and this desktop tool split Log_all.txt the same way.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent / "iw-solo-pixi-essential"
if (_PROJECT / "ingestion").is_dir() and str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from ingestion.splitter import (  # noqa: E402,F401
    ParsedRun,
    SplitReport,
    build_output_filename,
    classify_run,
    create_summary_file,
    ensure_unique_output_path,
    extract_mac_addresses,
    parse_run,
    split_log_file,
    split_run_blocks,
    summarize_output_directory,
)
