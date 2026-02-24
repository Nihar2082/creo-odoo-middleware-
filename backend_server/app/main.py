from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, conint
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import IDCounter, Part

app = FastAPI(title="Parts Backend", version="1.0.0")

# Allow local development. Tighten this in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tables on startup (simple approach). For production, consider migrations later.
Base.metadata.create_all(bind=engine)

API_KEY = os.getenv("API_KEY", "change-me")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

ID_PAD_WIDTH = int(os.getenv("ID_PAD_WIDTH", "6"))


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(api_key: Optional[str] = Security(api_key_header)) -> None:
    if not api_key or api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.get("/health", dependencies=[Depends(require_api_key)])
def health() -> Dict[str, str]:
    return {"status": "ok"}


class IDRequest(BaseModel):
    prefix: str = Field(..., min_length=1, max_length=20)
    count: conint(ge=1, le=1000)  # reserve up to 1000 at a time


class IDResponse(BaseModel):
    ids: List[str]


class PartSchema(BaseModel):
    external_id: str
    part_name: str
    internal_reference: Optional[str] = None
    item_type: Optional[str] = None
    qty: Optional[int] = None
    status: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None


@app.post("/ids/reserve", response_model=IDResponse, dependencies=[Depends(require_api_key)])
def reserve_ids(request: IDRequest, db: Session = Depends(get_db)) -> IDResponse:
    prefix = request.prefix.strip()

    # Transaction + row lock to be race-safe across users
    with db.begin():
        counter = db.execute(
            select(IDCounter).where(IDCounter.prefix == prefix).with_for_update()
        ).scalar_one_or_none()

        if counter is None:
            counter = IDCounter(prefix=prefix, next_value=1)
            db.add(counter)
            db.flush()

        start = counter.next_value
        end = start + int(request.count)
        counter.next_value = end

    ids = [f"{prefix}_{str(i).zfill(ID_PAD_WIDTH)}" for i in range(start, end)]
    return IDResponse(ids=ids)


@app.post("/parts/bulk_upsert", dependencies=[Depends(require_api_key)])
def bulk_upsert(parts: List[PartSchema], db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not parts:
        return {"status": "success", "upserted": 0}

    upserted = 0
    for p in parts:
        payload = p.model_dump()
        if not payload.get("internal_reference"):
            payload["internal_reference"] = payload["external_id"]

        existing = db.execute(select(Part).where(Part.external_id == payload["external_id"])).scalar_one_or_none()
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(Part(**payload))
        upserted += 1

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="Conflict while saving parts (duplicate external_id?)") from e

    return {"status": "success", "upserted": upserted}


@app.get("/parts", dependencies=[Depends(require_api_key)])
def list_parts(limit: int = 5000, offset: int = 0, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    Return parts stored in DB (for DB Viewer).
    Simple pagination using limit/offset.
    """
    q = select(Part).order_by(Part.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(q).scalars().all()
    return [r.to_dict() for r in rows]


@app.delete("/parts/{external_id}", dependencies=[Depends(require_api_key)])
def delete_part(external_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Permanently delete a part record by external_id.
    """
    existing = db.execute(select(Part).where(Part.external_id == external_id)).scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="Part not found")
    db.delete(existing)
    db.commit()
    return {"status": "deleted", "external_id": external_id}



@app.get("/parts", dependencies=[Depends(require_api_key)])
def list_parts(limit: int = 100, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    parts = db.execute(select(Part).order_by(Part.created_at.desc()).limit(limit)).scalars().all()
    return [p.to_dict() for p in parts]
