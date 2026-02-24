from __future__ import annotations
from dataclasses import dataclass
import re
from matching_logic.core.normalize import normalize_name

@dataclass(frozen=True)
class ModuleConfig:
    module_name: str
    prefix: str

def format_external_id(prefix: str, number: int, revision: str | None = None) -> str:
    base = f"{prefix}_{number:03d}"
    if revision:
        rev = normalize_name(revision)
        return f"{base}_{rev}"
    return base

def resolve_prefix(module_name: str, module_prefix_map: dict[str, str]) -> str:
    key = normalize_name(module_name)
    if key not in module_prefix_map:
        raise ValueError(f"Unknown module '{module_name}'. Add it to MODULE_PREFIX_MAP.")
    return module_prefix_map[key]


def normalize_prefix(prefix: str) -> str:
    """Normalize and validate a designer-provided prefix.

    Accepted format: A-Z and 0-9, must start with a letter, max length 10.
    Examples: PS, MD, STD, A1, M2026
    """
    p = (prefix or "").strip().upper()
    if not p:
        raise ValueError("Prefix cannot be empty.")
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", p):
        raise ValueError(
            "Prefix must start with a letter and contain only letters/numbers (max 10 chars)."
        )
    return p


def format_part_name(name: str, desired_prefix: str, add_prefix: bool = True) -> str:
    """Return the final Part Name for export/UI.

    Rules:
    - Always UPPERCASE.
    - If add_prefix is False => just return UPPERCASE.
    - If the name already starts with the desired prefix (e.g., desired_prefix='PS' and
      name starts with 'PS_') => leave it.
    - Otherwise => add '<PREFIX>_' in front.

    This matches your requirement: "include a prefix like PS, but if it's already a regular/standard
    part name (i.e., already prefixed), leave it alone."
    """
    n = normalize_name(name)
    if not add_prefix:
        return n

    p = normalize_prefix(desired_prefix)
    # Only keep the prefix if it's the *same* as the desired one.
    # (Names like 'EXTENSION_...' are not considered already prefixed for this purpose.)
    if n.startswith(f"{p}_"):
        return n
    return f"{p}_{n}"
