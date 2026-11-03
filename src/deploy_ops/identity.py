# src/deploy_ops/identity.py - Verify GitHub-issued identity before accepting a deployment request.

import time

import jwt
from pydantic import ValidationError

from .errors import Refused
from .models import Source

ISSUER = "https://token.actions.githubusercontent.com"
JWKS = ISSUER + "/.well-known/jwks"
CLAIMS = (
    "repository", "repository_id", "repository_owner_id", "ref", "sha", "actor",
    "actor_id", "run_id", "run_attempt", "event_name", "environment",
    "job_workflow_ref", "job_workflow_sha",
)


class GithubVerifier:
    """Use GitHub's fixed key endpoint rather than a URL supplied in the token."""

    def __init__(self, audience: str):
        self.audience = audience
        self.keys = jwt.PyJWKClient(JWKS, cache_jwk_set=True, lifespan=300, timeout=5)

    def verify(self, token: str) -> Source:
        if len(token) > 20000:
            raise Refused("Invalid workflow credential", 401)
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.audience, issuer=ISSUER,
                leeway=5,
                options={"require": ["iss", "sub", "aud", "iat", "nbf", "exp", "jti", *CLAIMS]},
            )
            if claims["exp"] - claims["iat"] > 900 or claims["iat"] > time.time() + 5:
                raise ValueError("Unexpected credential lifetime")
            return Source(**{key: claims[key] for key in CLAIMS}, subject=claims["sub"])
        except (jwt.PyJWTError, ValidationError, ValueError, TypeError, KeyError) as error:
            raise Refused("Invalid or expired workflow credential", 401) from error
