"""Pure generation and canonicalization of the synthetic credit dataset.

The correlation is deliberate and is not a claim about real French postcodes.
Each postcode subgroup has a disjoint income band and an increasing incident
range.  Exposing ``code_postal`` can therefore reveal this synthetic proxy in
the v2 experiment, while omitting it leaves the baseline prompt without it.
Profiles are seed-shuffled before IDs are assigned, so the ID is not a proxy.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from random import Random
from typing import Final, TypedDict

from domain.dossier import Dossier

DATASET_SEED: Final = 2_024_081_689
DATASET_SIZE: Final = 200
CENT: Final = Decimal("0.01")
LOAN_DURATIONS: Final = (12, 24, 36, 48, 60, 72, 84)
CONTRACT_TYPES: Final = ("CDI", "CDD", "INTERIM", "INDEPENDANT")
DOSSIER_FIELDS: Final = (
    "dossier_id",
    "revenu_mensuel",
    "charges_mensuelles",
    "montant_demande",
    "duree_mois",
    "anciennete_emploi_mois",
    "type_contrat",
    "age",
    "code_postal",
    "nb_incidents_passes",
)

type FinancialValues = tuple[str, Decimal, Decimal, Decimal, int]
type PersonalValues = tuple[int, str, int, str, int]
type DossierValues = tuple[str, Decimal, Decimal, Decimal, int, int, str, int, str, int]
type DossierPayload = dict[str, str | int]


class SubgroupSummary(TypedDict):
    count: int
    revenu_mensuel_moyen: str
    nb_incidents_passes_moyen: str
    postal_prefix_counts: dict[str, int]


class DatasetManifest(TypedDict):
    seed: int
    dataset_size: int
    dataset_sha256: str
    subgroups: dict[str, SubgroupSummary]


@dataclass(frozen=True, slots=True)
class SubgroupProfile:
    label: str
    postal_prefixes: tuple[str, ...]
    income_min: int
    income_max: int
    incidents_min: int
    incidents_max: int


# Bands intentionally encode a strong monotonic postcode proxy.
SUBGROUP_PROFILES: Final = (
    SubgroupProfile("A_REVENUS_ELEVES", ("75", "92"), 3_800, 5_200, 0, 0),
    SubgroupProfile("B_REVENUS_MOYENS", ("31", "69"), 2_600, 3_600, 0, 1),
    SubgroupProfile("C_REVENUS_MODESTES", ("13", "59"), 1_800, 2_500, 1, 2),
    SubgroupProfile("D_REVENUS_FAIBLES", ("62", "93"), 1_200, 1_700, 3, 5),
)


def _money(euros: int) -> Decimal:
    return Decimal(euros).quantize(CENT)


def _income(rng: Random, profile: SubgroupProfile) -> Decimal:
    return _money(rng.randint(profile.income_min, profile.income_max))


def _charges(rng: Random, income: Decimal) -> Decimal:
    ratio = Decimal(rng.randint(18, 58)) / Decimal(100)
    return (income * ratio).quantize(CENT)


def _postal_code(rng: Random, profile: SubgroupProfile) -> str:
    prefix = rng.choice(profile.postal_prefixes)
    return f"{prefix}{rng.randint(1, 999):03d}"


def _financial_values(
    index: int, rng: Random, profile: SubgroupProfile
) -> FinancialValues:
    income = _income(rng, profile)
    amount = _money(rng.randint(2_000, 35_000))
    duration = rng.choice(LOAN_DURATIONS)
    return str(index + 1), income, _charges(rng, income), amount, duration


def _personal_values(rng: Random, profile: SubgroupProfile) -> PersonalValues:
    incidents = rng.randint(profile.incidents_min, profile.incidents_max)
    return (
        rng.randint(0, 240),
        rng.choice(CONTRACT_TYPES),
        rng.randint(21, 69),
        _postal_code(rng, profile),
        incidents,
    )


def _dossier_values(index: int, rng: Random, profile: SubgroupProfile) -> DossierValues:
    return (*_financial_values(index, rng, profile), *_personal_values(rng, profile))


def _generate_dossier(index: int, rng: Random, profile: SubgroupProfile) -> Dossier:
    return Dossier(*_dossier_values(index, rng, profile))


def _profile_schedule(rng: Random, count: int) -> tuple[SubgroupProfile, ...]:
    profiles = [
        SUBGROUP_PROFILES[index % len(SUBGROUP_PROFILES)] for index in range(count)
    ]
    rng.shuffle(profiles)
    return tuple(profiles)


def generate_dossiers(
    seed: int = DATASET_SEED, count: int = DATASET_SIZE
) -> tuple[Dossier, ...]:
    rng = Random(seed)
    profiles = _profile_schedule(rng, count)
    return tuple(
        _generate_dossier(index, rng, profile) for index, profile in enumerate(profiles)
    )


def subgroup_for_postal_code(code_postal: str) -> str:
    for profile in SUBGROUP_PROFILES:
        if code_postal.startswith(profile.postal_prefixes):
            return profile.label
    raise ValueError(f"unknown synthetic postcode subgroup: {code_postal}")


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(CENT), "f")


def _financial_text(dossier: Dossier) -> tuple[str, str, str]:
    values = dossier.revenu_mensuel, dossier.charges_mensuelles, dossier.montant_demande
    return (
        _decimal_text(values[0]),
        _decimal_text(values[1]),
        _decimal_text(values[2]),
    )


def _personal_payload_values(dossier: Dossier) -> PersonalValues:
    return (
        dossier.anciennete_emploi_mois,
        dossier.type_contrat,
        dossier.age,
        dossier.code_postal,
        dossier.nb_incidents_passes,
    )


def _payload_values(dossier: Dossier) -> tuple[str | int, ...]:
    income, charges, amount = _financial_text(dossier)
    head = dossier.dossier_id, income, charges, amount, dossier.duree_mois
    return (*head, *_personal_payload_values(dossier))


def dossier_to_payload(dossier: Dossier) -> DossierPayload:
    return dict(zip(DOSSIER_FIELDS, _payload_values(dossier), strict=True))


def dataset_to_payload(dossiers: Sequence[Dossier]) -> list[DossierPayload]:
    return [dossier_to_payload(dossier) for dossier in dossiers]


def canonical_dataset_json(dossiers: Sequence[Dossier]) -> str:
    return json.dumps(
        dataset_to_payload(dossiers),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def dataset_sha256(dossiers: Sequence[Dossier]) -> str:
    canonical = canonical_dataset_json(dossiers).encode("utf-8")
    return sha256(canonical).hexdigest()


def _dossiers_for_profile(
    dossiers: Sequence[Dossier], profile: SubgroupProfile
) -> tuple[Dossier, ...]:
    return tuple(
        dossier
        for dossier in dossiers
        if subgroup_for_postal_code(dossier.code_postal) == profile.label
    )


def _average_income(dossiers: Sequence[Dossier]) -> str:
    total = sum((dossier.revenu_mensuel for dossier in dossiers), Decimal())
    return _decimal_text(total / len(dossiers))


def _average_incidents(dossiers: Sequence[Dossier]) -> str:
    total = sum(dossier.nb_incidents_passes for dossier in dossiers)
    return _decimal_text(Decimal(total) / len(dossiers))


def _prefix_counts(
    dossiers: Sequence[Dossier], profile: SubgroupProfile
) -> dict[str, int]:
    return {
        prefix: sum(dossier.code_postal.startswith(prefix) for dossier in dossiers)
        for prefix in profile.postal_prefixes
    }


def _group_summary(
    dossiers: Sequence[Dossier], profile: SubgroupProfile
) -> SubgroupSummary:
    return {
        "count": len(dossiers),
        "revenu_mensuel_moyen": _average_income(dossiers),
        "nb_incidents_passes_moyen": _average_incidents(dossiers),
        "postal_prefix_counts": _prefix_counts(dossiers, profile),
    }


def subgroup_distribution(dossiers: Sequence[Dossier]) -> dict[str, SubgroupSummary]:
    return {
        profile.label: _group_summary(group, profile)
        for profile in SUBGROUP_PROFILES
        if (group := _dossiers_for_profile(dossiers, profile))
    }


def build_dataset_manifest(
    dossiers: Sequence[Dossier], seed: int = DATASET_SEED
) -> DatasetManifest:
    return {
        "seed": seed,
        "dataset_size": len(dossiers),
        "dataset_sha256": dataset_sha256(dossiers),
        "subgroups": subgroup_distribution(dossiers),
    }
