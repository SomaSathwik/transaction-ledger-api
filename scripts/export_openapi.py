"""Write the generated OpenAPI specification to docs/openapi.json.

Usage (from the project root):  python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402  (import after sys.path tweak)

target = ROOT / "docs" / "openapi.json"
target.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"OpenAPI specification written to {target}")
