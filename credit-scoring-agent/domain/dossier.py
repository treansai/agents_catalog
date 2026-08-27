from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Dossier:
    dossier_id: str
    revenu_mensuel: Decimal
    charges_mensuelles: Decimal
    montant_demande: Decimal
    duree_mois: int
    anciennete_emploi_mois: int
    type_contrat: str
    age: int
    code_postal: str
    nb_incidents_passes: int


@dataclass(frozen=True, slots=True)
class DebtRatio:
    taux_endettement: Decimal
    reste_a_vivre: Decimal
