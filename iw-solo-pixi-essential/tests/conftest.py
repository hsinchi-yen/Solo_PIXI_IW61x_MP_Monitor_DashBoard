import sys
from pathlib import Path

# The API modules import each other as top-level modules (`import config`, `from db import db`),
# the same way uvicorn loads them inside the container.
API_DIR = Path(__file__).resolve().parents[1] / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
