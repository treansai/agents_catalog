"""Erreurs HTTP : seul le statut compte, le corps est toujours l'enveloppe neutre."""

from __future__ import annotations


class HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status
