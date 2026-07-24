# Spec: IW611 Log Splitter

## Objective

Create a PyQt5 desktop application named `iw_log_splitter_app.py` that splits an
IW611 `Log_all.txt` into one output file per IQfact `Timestamp` Run.

The implementation keeps the reference application's core workflow: source and
output selection, background processing, progress messages, result statistics,
and `summary.txt` generation.

## Approved Rules

- Treat every IQfact block containing `======== Timestamp:` as an independent
  output record.
- Use the block's own timestamp (the final timestamp for that independent Run).
- Classify a Run as:
  - `PASS` when the final summary reports `Passed Run(s) > 0`.
  - `FAIL` when the final summary reports failed runs or a final flow-running
    error.
  - `STOP` when no conclusive final PASS/FAIL summary exists.
- Extract:
  - MAC1 from `Generetaed MAC Address` (with `MAC_ADDRESS` fallback).
  - MAC2 from `BD_ADDRESS`.
- Remove MAC separators and convert hexadecimal characters to uppercase.
- Use `UNKNOWNMAC1` or `UNKNOWNMAC2` when an incomplete STOP record has no
  usable address; do not invent a hardware address.
- Name files as `YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt`.
- Preserve the source block content without reformatting its internal lines.
- Never overwrite an existing output file; append `_1`, `_2`, and so on.
- Verification output goes to the source directory's `split_output` folder.

## Tech Stack

- Python 3.10+
- Standard library for parsing, filesystem operations, and tests
- PyQt5 for the desktop GUI (matching the reference application)

No new runtime dependency is introduced beyond the PyQt5 dependency already
used by the reference GUI.

## Commands

```powershell
# Run the GUI
python Utilities/iw_log_splitter_app.py

# Run focused tests
python Utilities/test_iw_log_splitter.py -v

# Run the existing dashboard regression suite
python Ref_Project/test_dashboard_suite.py
```

## Project Structure

```text
Utilities/iw_log_splitter_core.py       Pure parser, splitter, statistics, summary writer
Utilities/iw_log_splitter_app.py        PyQt5 GUI and background worker
Utilities/test_iw_log_splitter.py       Unit and file-integration tests
docs/iw_log_splitter_spec.md  Approved behavior and acceptance criteria
```

## Code Style

Use small typed functions for pure parsing rules and keep GUI concerns outside
the core module.

```python
def classify_run(block: str) -> str:
    """Return PASS, FAIL, or STOP from the Run's final summary."""
```

Constants use uppercase names, public functions use `snake_case`, and filesystem
inputs are accepted as `os.PathLike` values.

## Testing Strategy

- Unit tests cover Run boundaries, timestamp formatting, MAC normalization,
  PASS/FAIL/STOP precedence, and unique filenames.
- File-integration tests split temporary logs and verify exact output content
  plus summary counts.
- The real `Log_all.txt` is processed after unit tests pass.
- The provided PASS and FAIL templates are used as validation evidence, while
  respecting the approved one-Timestamp-per-file rule.

## Boundaries

- Always:
  - Validate source and output paths.
  - Keep parsing independent of PyQt5.
  - Avoid overwriting existing output files.
  - Keep the GUI responsive with a worker thread.
- Ask first:
  - Change the output naming convention.
  - Group multiple Timestamp Runs into one file.
  - Add packaging or third-party dependencies.
- Never:
  - Modify or delete the source log.
  - Rewrite the provided reference/template files.
  - Treat transient in-Run `[FAIL]` messages as the final result when a final
    PASS summary exists.

## Success Criteria

- The GUI starts from `Utilities/iw_log_splitter_app.py`.
- `Log_all.txt` produces one file per detected Timestamp Run.
- Output filenames follow the approved format and use normalized MAC values.
- Incomplete Runs are retained as STOP, including records with missing MACs.
- The UI reports total, PASS, FAIL, STOP, skipped, and output counts.
- `summary.txt` can be generated from the output directory.
- Focused tests and the existing regression suite pass.
- Real verification completes in `rawlogs/.../30/split_output`.
