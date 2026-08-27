"""Write the deterministic synthetic dataset and its manifest."""

import json
from pathlib import Path

from domain.dataset import (
    DATASET_SEED,
    build_dataset_manifest,
    dataset_to_payload,
    generate_dossiers,
)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data"


def _json_text(value: object) -> str:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(_json_text(value), encoding="utf-8")


def generate_dataset_files(output_dir: Path = DEFAULT_OUTPUT_DIR) -> tuple[Path, Path]:
    dossiers = generate_dossiers()
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_dir / "dossiers.json"
    manifest_path = output_dir / "dataset_manifest.json"
    _write_json(dataset_path, dataset_to_payload(dossiers))
    _write_json(manifest_path, build_dataset_manifest(dossiers, DATASET_SEED))
    return dataset_path, manifest_path


def main() -> None:
    dataset_path, manifest_path = generate_dataset_files()
    print(f"Dataset: {dataset_path}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
