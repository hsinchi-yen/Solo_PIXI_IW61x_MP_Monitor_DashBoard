import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from parsers.iw61x import audit_folder

def main():
    log_dir = WORKSPACE_ROOT / "rawlogs" / "5101-260715003"
    report = audit_folder(log_dir)
    print(json.dumps(report, indent=2))
    assert report["total_files"] == 180
    assert report["results"] == {"PASS": 121, "FAIL": 13, "STOP": 46}
    assert report["unknown_mac_files"] == 7
    assert report["work_orders"] == {"5101-260715003": 180}
    assert report["errors"] == []

if __name__ == "__main__":
    main()
