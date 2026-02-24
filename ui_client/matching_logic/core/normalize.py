from __future__ import annotations


def normalize_name(name: str) -> str:
    """Normalize a part name for matching/storage."""
    return (name or "").strip().upper()


def strip_leading_prefix_token(name: str) -> str:
    """Strip a leading '<TOKEN>_' prefix from a part name.

    Example:
      'PS_FRAME' -> 'FRAME'

    We only strip ONE leading token, and only if it looks like a reasonable
    designer prefix token (starts with a letter, max 10 chars, alnum).
    """
    n = normalize_name(name)
    if "_" not in n:
        return n
    token, rest = n.split("_", 1)
    if not rest:
        return n
    if 1 <= len(token) <= 10 and token[:1].isalpha() and token.isalnum():
        return rest
    return n


def canonical_key(part_name: str, revision: str | None = None) -> str:
    """Build a stable matching key for a part.

    Policy:
    - Ignore any designer prefix token in the name (PS_/MD_/...)
    - Do NOT include item type. Item type/category is user-assigned and may
      vary across files; matching should primarily be name-based.
    - Revision is optional; include it only if explicitly provided.
    """
    base = strip_leading_prefix_token(part_name)
    if revision:
        return f"{base}|{normalize_name(revision)}"
    return base
