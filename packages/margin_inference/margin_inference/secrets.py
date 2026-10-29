from __future__ import annotations

import os

from margin_inference.types import InferencePolicyError


def resolve_secret(secret_reference: str | None) -> str | None:
    """Resolve a secret reference at call time without logging the reference or value.

    Resolution order:
    1. If None or empty, return None.
    2. If starts with 'env:', look up the environment variable named after 'env:'.
       Raises InferencePolicyError if that environment variable is not set.
    3. If contains ':' with any other scheme, raises InferencePolicyError.
    4. Otherwise treat the reference as an environment variable name.
    """
    if secret_reference is None:
        return None
    cleaned = secret_reference.strip()
    if not cleaned:
        return None
    if cleaned.startswith("env:"):
        env_var = cleaned[4:].strip()
        val = os.getenv(env_var)
        if val is None:
            raise InferencePolicyError(f"referenced environment variable '{env_var}' is not set")
        return val
    if ":" in cleaned:
        scheme = cleaned.split(":", 1)[0]
        raise InferencePolicyError(f"unsupported secret reference scheme in '{scheme}'")
    value = os.getenv(cleaned)
    if value is None:
        raise InferencePolicyError(f"referenced environment variable '{cleaned}' is not set")
    return value
