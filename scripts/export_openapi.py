"""Export the OpenAPI schema to docs/openapi.json (and .yaml) for integrators.

The spec is generated from the live FastAPI app, so it can never drift from the
routes as long as this is re-run. CI runs it with ``--check`` to fail the build
when a route or model changed without the committed spec being regenerated.

Usage::

    python scripts/export_openapi.py            # write docs/openapi.{json,yaml}
    python scripts/export_openapi.py --check     # exit 1 if the committed spec is stale

The WebSocket endpoint is not expressible in OpenAPI 3.1, so it is documented as
an ``x-websockets`` vendor extension rather than being silently omitted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS_DIR = ROOT / "docs"
JSON_PATH = DOCS_DIR / "openapi.json"
YAML_PATH = DOCS_DIR / "openapi.yaml"

# The streaming contract lives outside OpenAPI; publish it as a vendor extension
# so a client generator still sees the frame types it must handle.
WEBSOCKET_SPEC = {
    "/ws/insights/{symbol}": {
        "summary": "Streaming AI insight, live alerts and market-wide event frames.",
        "auth": "JWT access token via the ?token= query parameter.",
        "frames": {
            "start": "Stream opened for a symbol.",
            "token": "Incremental text chunk of the model's rationale.",
            "end": "Stream complete; carries citations and the disclaimer.",
            "error": "Recoverable stream error; the socket stays open.",
            "alert": "A user alert fired (price/RSI/model condition).",
            "event": "Market-wide event intelligence broadcast to every socket.",
        },
    }
}


def build_spec() -> dict:
    """Generate the OpenAPI document from the app, without booting the lifespan."""
    os.environ.setdefault("ENVIRONMENT", "development")
    from app.main import create_app

    app = create_app()
    spec = app.openapi()
    spec["info"]["description"] = (
        "AI-assisted market intelligence and paper-trading execution platform.\n\n"
        "Every AI-backed endpoint degrades safely: with no model key configured "
        "the quantitative readings still return and responses are flagged "
        "`degraded`. Outputs are research signals, not investment advice."
    )
    spec["x-websockets"] = WEBSOCKET_SPEC
    return spec


def render_json(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_yaml(spec: dict) -> str:
    import yaml

    return yaml.safe_dump(spec, sort_keys=True, allow_unicode=True, width=100)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed spec matches the app instead of writing it.",
    )
    args = parser.parse_args()

    spec = build_spec()
    json_text, yaml_text = render_json(spec), render_yaml(spec)

    if args.check:
        stale = [
            path.name
            for path, text in ((JSON_PATH, json_text), (YAML_PATH, yaml_text))
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
        if stale:
            print(
                "OpenAPI spec is out of date: "
                + ", ".join(stale)
                + "\nRegenerate with: python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        print("OpenAPI spec is up to date.")
        return 0

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json_text, encoding="utf-8")
    YAML_PATH.write_text(yaml_text, encoding="utf-8")
    print(
        f"Wrote {JSON_PATH.relative_to(ROOT)} and {YAML_PATH.relative_to(ROOT)} "
        f"({len(spec.get('paths', {}))} paths)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
