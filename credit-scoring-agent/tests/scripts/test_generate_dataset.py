import json
from hashlib import sha256
from pathlib import Path

from domain.dataset import DATASET_SEED, DATASET_SIZE
from scripts.generate_dataset import generate_dataset_files


def _dataset_hash(dataset: object) -> str:
    canonical = json.dumps(
        dataset,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def test_generated_file_names(tmp_path: Path) -> None:
    dataset_path, manifest_path = generate_dataset_files(tmp_path)
    assert dataset_path.name == "dossiers.json"
    assert manifest_path.name == "dataset_manifest.json"


def test_generated_dataset_matches_manifest(tmp_path: Path) -> None:
    dataset_path, manifest_path = generate_dataset_files(tmp_path)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(dataset) == DATASET_SIZE
    assert manifest["seed"] == DATASET_SEED
    assert manifest["dataset_size"] == DATASET_SIZE
    assert _dataset_hash(dataset) == manifest["dataset_sha256"]
