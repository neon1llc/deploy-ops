# src/deploy_ops/errors.py - Carry safe errors across API and command line boundaries.


class Refused(Exception):
    """Stop a request without exposing credentials or provider responses."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status
