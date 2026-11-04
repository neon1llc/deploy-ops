# src/deploy_ops/errors.py - Carry safe errors across API and command line boundaries.


# Carry an intentional failure safely across interface boundaries
class Refused(Exception):
    """Stop a request without exposing credentials or provider responses."""


    # Keep the public message separate from its HTTP status
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status
