from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from domain.dossier import DebtRatio, Dossier
from domain.scoring import compute_debt_ratio

BASE_DOSSIER = Dossier(
    dossier_id="147",
    revenu_mensuel=Decimal("3000.00"),
    charges_mensuelles=Decimal("900.00"),
    montant_demande=Decimal("12000.00"),
    duree_mois=48,
    anciennete_emploi_mois=36,
    type_contrat="CDI",
    age=34,
    code_postal="75015",
    nb_incidents_passes=0,
)


def test_compute_debt_ratio_returns_percentage_and_remaining_income() -> None:
    result = compute_debt_ratio(BASE_DOSSIER)
    assert result == DebtRatio(Decimal("30.00"), Decimal("2100.00"))


@pytest.mark.parametrize(
    ("charges", "expected_ratio", "expected_remaining"),
    [
        ("0", "0.00", "3000.00"),
        ("3000", "100.00", "0.00"),
        ("3600", "120.00", "-600.00"),
        ("1", "0.03", "2999.00"),
    ],
)
def test_compute_debt_ratio_handles_boundaries(
    charges: str, expected_ratio: str, expected_remaining: str
) -> None:
    dossier = replace(BASE_DOSSIER, charges_mensuelles=Decimal(charges))
    result = compute_debt_ratio(dossier)
    assert result.taux_endettement == Decimal(expected_ratio)
    assert result.reste_a_vivre == Decimal(expected_remaining)


@pytest.mark.parametrize("income", ["0", "-1"])
def test_compute_debt_ratio_rejects_non_positive_income(income: str) -> None:
    dossier = replace(BASE_DOSSIER, revenu_mensuel=Decimal(income))
    with pytest.raises(ValueError, match="revenu_mensuel"):
        compute_debt_ratio(dossier)


def test_compute_debt_ratio_rejects_negative_charges() -> None:
    dossier = replace(BASE_DOSSIER, charges_mensuelles=Decimal("-0.01"))
    with pytest.raises(ValueError, match="charges_mensuelles"):
        compute_debt_ratio(dossier)


@pytest.mark.parametrize(
    ("value", "field_name"),
    [
        (BASE_DOSSIER, "dossier_id"),
        (compute_debt_ratio(BASE_DOSSIER), "taux_endettement"),
    ],
)
def test_financial_values_are_immutable(value: object, field_name: str) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(value, field_name, "changed")
