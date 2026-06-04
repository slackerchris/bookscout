"""Export the FastAPI OpenAPI schema as JSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app


def main() -> None:
    schema = app.openapi()
    payload = json.dumps(schema, indent=2, sort_keys=True)

    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        return

    print(payload)


if __name__ == "__main__":
    main()
