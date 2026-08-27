import argparse
import json
import sys
from pathlib import Path
from typing import Any

from models import LeadQualificationOutput
from workflow import qualify_lead


def _load_payload(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Input JSON must be an object.")
    if "lead" not in payload or not isinstance(payload["lead"], dict):
        raise ValueError("Input JSON must include a 'lead' object.")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qualify an inbound lead from a JSON payload."
    )
    parser.add_argument("input_json", help="Path to a JSON file with lead, behavior, and enrichment.")
    args = parser.parse_args(argv)

    try:
        payload = _load_payload(Path(args.input_json))
        output = qualify_lead(
            lead=payload["lead"],
            behavior=payload.get("behavior", {}),
            enrichment=payload.get("enrichment", {}),
        )
        validated = LeadQualificationOutput.model_validate(output)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(validated.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
