from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class EBOMRow:
    qty: float
    name: str
    item_type: str
    revision: Optional[str] = None
    description: Optional[str] = None

@dataclass
class MatchSuggestion:
    external_id: str
    name: str
    score: float
    reason: str

@dataclass
class ProcessedRow:
    qty: float
    name: str
    item_type: str
    revision: Optional[str] = None
    description: Optional[str] = None

    # User-editable fields
    price: Optional[float] = None

    status: str = "NEW"  # NEW | EXISTING | POSSIBLE_MATCH | CREATED
    external_id: Optional[str] = None  # also used as Odoo default_code
    suggestions: List[MatchSuggestion] = field(default_factory=list)

    # Stable key used for matching across sessions (ignores designer prefix).
    canonical_key: str = ""

    # For POSSIBLE_MATCH rows: user decision during review.
    # - None: not reviewed yet
    # - "REJECT": treat as NEW
    # - <external_id>: confirm match to that existing part
    match_decision: Optional[str] = None

    # If True, generate IDs using the special STD prefix.
    # Otherwise, use the designer-provided regular prefix (PS/MD/...).
    is_standard: bool = False

    included: bool = True  # export filter
