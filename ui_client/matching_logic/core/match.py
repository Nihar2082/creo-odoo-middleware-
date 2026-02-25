from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional

from matching_logic.core.normalize import normalize_name, canonical_key


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def match_row(
    name: str,
    registry: Dict,
    threshold: float = 0.80,
    max_suggestions: int = 5,
) -> Tuple[str, Optional[str], List[Tuple[str, float, str]]]:
    """Return (status, external_id_if_existing, suggestions).

    Matching policy:
    - Primary match is name-based (after stripping any designer prefix token).
    - Item type/category is user-assigned and may vary by file, so it is NOT
      part of the stable match key.
    - Lower threshold (0.80 instead of 0.85) for better detection of similar parts.

    registry expects:
      registry["index"]: dict[key] -> external_id (exact hits)
      registry["parts"]: list of dict {external_id, name_norm, canonical_key, ...}
    """
    name_norm = normalize_name(name)
    ckey = canonical_key(name)
    # 0) exact index hit (normalized name or canonical key)
    ext = registry.get("index", {}).get(name_norm) or registry.get("index", {}).get(ckey)
    if ext:
        return "EXISTING", ext, []

    # 1) exact canonical match
    for p in registry.get("parts", []):
        if (p.get("canonical_key") or "") == ckey:
            return "EXISTING", p["external_id"], []

    # 1b) exact name match (fallback)
    for p in registry.get("parts", []):
        if (p.get("name_norm") or "") == name_norm:
            return "EXISTING", p["external_id"], []

    # 2) similarity suggestions - now with improved matching
    candidates: List[Tuple[str, float, str]] = []
    for p in registry.get("parts", []):
        target = p.get("canonical_key") or p.get("name_norm") or ""
        s = similarity(ckey, target)
        if s >= threshold:
            candidates.append((p["external_id"], s, f"name similarity {s:.2f}"))

    candidates.sort(key=lambda x: x[1], reverse=True)
    if candidates:
        return "POSSIBLE_MATCH", None, candidates[:max_suggestions]

    return "NEW", None, []
