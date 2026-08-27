import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from domain.dataset import (
    DATASET_SEED,
    DATASET_SIZE,
    SUBGROUP_PROFILES,
    build_dataset_manifest,
    canonical_dataset_json,
    dataset_sha256,
    dataset_to_payload,
    dossier_to_payload,
    generate_dossiers,
    subgroup_distribution,
    subgroup_for_postal_code,
)


def test_default_dataset_is_reproducible_and_has_exactly_200_dossiers() -> None:
    first = generate_dossiers()
    second = generate_dossiers(DATASET_SEED, DATASET_SIZE)
    assert first == second
    assert len(first) == 200
    assert len({dossier.dossier_id for dossier in first}) == 200
    assert first[0].dossier_id == "1"
    assert first[-1].dossier_id == "200"


def test_seed_changes_the_generated_dataset() -> None:
    assert generate_dossiers(1) != generate_dossiers(2)


def test_dossier_id_modulo_is_not_a_postcode_subgroup_proxy() -> None:
    dossiers = generate_dossiers()
    associations = {
        (int(dossier.dossier_id) % 4, subgroup_for_postal_code(dossier.code_postal))
        for dossier in dossiers
    }
    assert len(associations) == 16


def test_generated_financial_fields_stay_inside_synthetic_bounds() -> None:
    dossiers = generate_dossiers()
    assert all(dossier.revenu_mensuel > 0 for dossier in dossiers)
    assert all(
        0 <= dossier.charges_mensuelles < dossier.revenu_mensuel for dossier in dossiers
    )
    assert all(
        dossier.duree_mois in {12, 24, 36, 48, 60, 72, 84} for dossier in dossiers
    )


def test_generated_personal_fields_stay_inside_synthetic_bounds() -> None:
    dossiers = generate_dossiers()
    assert all(21 <= dossier.age <= 69 for dossier in dossiers)
    assert all(len(dossier.code_postal) == 5 for dossier in dossiers)


def test_postcode_profiles_encode_income_and_incident_correlation() -> None:
    summaries = subgroup_distribution(generate_dossiers())
    ordered = [summaries[profile.label] for profile in SUBGROUP_PROFILES]
    incomes = [Decimal(str(summary["revenu_mensuel_moyen"])) for summary in ordered]
    incidents = [Decimal(str(item["nb_incidents_passes_moyen"])) for item in ordered]
    assert incomes == sorted(incomes, reverse=True)
    assert incidents == sorted(incidents)
    assert len(set(incomes)) == len(SUBGROUP_PROFILES)
    assert len(set(incidents)) == len(SUBGROUP_PROFILES)


def test_each_subgroup_contains_50_dossiers_and_both_prefixes() -> None:
    summaries = subgroup_distribution(generate_dossiers())
    assert {summary["count"] for summary in summaries.values()} == {50}
    for summary in summaries.values():
        counts = summary["postal_prefix_counts"]
        assert isinstance(counts, dict)
        assert len(counts) == 2
        assert all(count > 0 for count in counts.values())


@pytest.mark.parametrize(
    ("code_postal", "expected"),
    [
        (profile.postal_prefixes[0] + "001", profile.label)
        for profile in SUBGROUP_PROFILES
    ],
)
def test_subgroup_is_derived_from_postcode(code_postal: str, expected: str) -> None:
    assert subgroup_for_postal_code(code_postal) == expected


def test_unknown_postcode_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown synthetic postcode subgroup"):
        subgroup_for_postal_code("00000")


def test_empty_dataset_has_no_subgroup_distribution() -> None:
    assert subgroup_distribution(()) == {}


def test_dataset_serialization_preserves_money_and_uses_canonical_json() -> None:
    dossier = generate_dossiers(count=1)[0]
    payload = dossier_to_payload(dossier)
    canonical = canonical_dataset_json((dossier,))
    income = payload["revenu_mensuel"]
    assert isinstance(income, str)
    assert income.endswith(".00")
    assert json.loads(canonical) == dataset_to_payload((dossier,))
    assert canonical.startswith('[{"age":')
    assert " " not in canonical


def test_dataset_hash_is_sha256_of_utf8_canonical_payload() -> None:
    dossiers = generate_dossiers(count=2)
    assert len(dataset_sha256(dossiers)) == 64
    assert dataset_sha256(dossiers) != dataset_sha256(tuple(reversed(dossiers)))


def test_default_dataset_hash_is_stable() -> None:
    expected = "c354416c25301017f2becb1daead37365d0e0f98440ba3f64f398b19e1669852"
    assert dataset_sha256(generate_dossiers()) == expected


def test_manifest_records_seed_size_hash_and_subgroup_distribution() -> None:
    dossiers = generate_dossiers()
    manifest = build_dataset_manifest(dossiers)
    assert manifest["seed"] == DATASET_SEED
    assert manifest["dataset_size"] == DATASET_SIZE
    assert manifest["dataset_sha256"] == dataset_sha256(dossiers)
    assert manifest["subgroups"] == subgroup_distribution(dossiers)


def test_subgroup_profiles_are_immutable() -> None:
    field_name = "label"
    with pytest.raises(FrozenInstanceError):
        setattr(SUBGROUP_PROFILES[0], field_name, "changed")
