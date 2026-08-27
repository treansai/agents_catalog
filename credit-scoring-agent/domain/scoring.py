from decimal import ROUND_HALF_UP, Decimal

from domain.dossier import DebtRatio, Dossier

PERCENT = Decimal("100")
PERCENT_PRECISION = Decimal("0.01")


def _validate_financial_inputs(dossier: Dossier) -> None:
    if dossier.revenu_mensuel <= 0:
        raise ValueError("revenu_mensuel must be positive")
    if dossier.charges_mensuelles < 0:
        raise ValueError("charges_mensuelles must not be negative")


def compute_debt_ratio(dossier: Dossier) -> DebtRatio:
    _validate_financial_inputs(dossier)
    ratio = dossier.charges_mensuelles / dossier.revenu_mensuel * PERCENT
    rounded_ratio = ratio.quantize(PERCENT_PRECISION, rounding=ROUND_HALF_UP)
    remaining = dossier.revenu_mensuel - dossier.charges_mensuelles
    return DebtRatio(rounded_ratio, remaining)
