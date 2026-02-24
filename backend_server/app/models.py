from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .database import Base


class Part(Base):
    __tablename__ = "parts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id = Column(String, unique=True, nullable=False, index=True)

    part_name = Column(String, nullable=False)
    internal_reference = Column(String, nullable=True)
    item_type = Column(String, nullable=True)

    qty = Column(Integer, nullable=True)
    status = Column(String, nullable=True)

    # Dynamic/custom columns live here
    data = Column(JSONB, nullable=False, default=dict)

    created_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "external_id": self.external_id,
            "part_name": self.part_name,
            "internal_reference": self.internal_reference,
            "item_type": self.item_type,
            "qty": self.qty,
            "status": self.status,
            "data": self.data or {},
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IDCounter(Base):
    __tablename__ = "id_counters"

    prefix = Column(String, primary_key=True)
    next_value = Column(Integer, nullable=False)
