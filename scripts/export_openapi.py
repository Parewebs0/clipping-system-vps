"""Export the FastAPI OpenAPI document to frontend/openapi.json.

The dashboard generates its TypeScript types from this file
(`npm run gen:api` in frontend/). tests/test_openapi_export.py fails when the
committed file is stale, so run this after changing any response model:

    python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "frontend" / "openapi.json"


def build() -> str:
    # Importing the app needs settings but no DB connection.
    os.environ.setdefault("CLIPPING_DB_NAME", "openapi_export_test")
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
