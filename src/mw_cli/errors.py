class WikiError(Exception):
    """A machine-readable error, deliberately excluding secrets from API messages."""

    def __init__(self, code, message, *, details=None, exit_code=1):
        super().__init__(message)
        self.code = code
        self.details = details
        self.exit_code = exit_code


def require(condition, message):
    if not condition:
        raise WikiError("invalid-input", message, exit_code=2)
