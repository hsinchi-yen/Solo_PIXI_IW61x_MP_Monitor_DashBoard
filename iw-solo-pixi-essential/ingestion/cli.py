from __future__ import annotations

import argparse
import json

from ingestion.service import InMemoryRunRepository, ingest_paths
from parsers.iw61x import audit_folder


def main() -> int:
    parser = argparse.ArgumentParser(description="IW611 IQfact log ingestion utility")
    parser.add_argument("paths", nargs="+", help="Log files or folders")
    parser.add_argument("--work-order", help="Optional work-order override")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing to PostgreSQL")
    args = parser.parse_args()

    if args.dry_run:
        if len(args.paths) == 1:
            print(json.dumps(audit_folder(args.paths[0], args.work_order), indent=2))
        else:
            repo = InMemoryRunRepository()
            print(json.dumps(ingest_paths(args.paths, repo, args.work_order, "cli-dry-run").as_dict(), indent=2))
        return 0

    raise SystemExit("Database CLI upload is provided through the API/GUI in this release. Use --dry-run for parser audits.")


if __name__ == "__main__":
    raise SystemExit(main())
