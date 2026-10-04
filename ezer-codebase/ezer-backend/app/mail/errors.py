class MailConnectionError(Exception):
    """Erreur interne : seul son `code` stable sort du service, jamais un message fournisseur."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
