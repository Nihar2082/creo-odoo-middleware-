from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, conint
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import IDCounter, Part, UiSetting, ItemCategory

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
def list_parts(
    limit: conint(ge=1, le=5000) = 500,
    offset: int = 0,
    q: Optional[str] = None,
    paged: bool = False,
    db: Session = Depends(get_db),
) -> Any:
    """
    Return parts stored in DB (for DB Viewer and UI registry).

    - Supports pagination with limit/offset
    - Optional simple search with `q`
    - If `paged=true`, returns a wrapper with {items,total,limit,offset,next_offset}
      (otherwise returns a plain list for backward compatibility).
    """
    base = select(Part)

    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                Part.external_id.ilike(like),
                Part.part_name.ilike(like),
                Part.internal_reference.ilike(like),
                Part.item_type.ilike(like),
            )
        )

    # Total for pagination (fast enough for moderate sizes; add an index strategy if DB gets huge)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()

    q_parts = base.order_by(Part.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(q_parts).scalars().all()
    items = [r.to_dict() for r in rows]

    if paged:
        next_offset = offset + limit if (offset + limit) < total else None
        return {"items": items, "total": total, "limit": limit, "offset": offset, "next_offset": next_offset}

    return items






class CandidateQuery(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    internal_reference: Optional[str] = Field(None, max_length=200)
    item_type: Optional[str] = Field(None, max_length=100)
    limit: conint(ge=1, le=200) = 50


class BulkCandidateQuery(BaseModel):
    queries: List[CandidateQuery]
    global_limit: conint(ge=1, le=200) = 50


def _candidate_search(db: Session, name: str, internal_reference: Optional[str], item_type: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """Return a small set of likely candidates for matching.

    Strategy (simple + index-friendly):
    - Use ILIKE on part_name + internal_reference + external_id
    - Prefer direct hits on internal_reference/external_id
    - Limit results aggressively (client does local scoring on this subset)
    """
    name = (name or "").strip()
    internal_reference = (internal_reference or "").strip() or None
    item_type = (item_type or "").strip() or None

    if not name and not internal_reference:
        return []

    # Prefer internal_reference as a strong identifier when provided
    clauses = []
    if internal_reference:
        like_ir = f"%{internal_reference}%"
        clauses.append(Part.internal_reference.ilike(like_ir))
        clauses.append(Part.external_id.ilike(like_ir))

    if name:
        like_name = f"%{name}%"
        clauses.append(Part.part_name.ilike(like_name))

        # Also try tokenized search for long names: all tokens must appear somewhere
        toks = [t for t in re.split(r"\s+", name) if len(t) >= 3][:8]
        for t in toks:
            clauses.append(Part.part_name.ilike(f"%{t}%"))

    q = select(Part).where(or_(*clauses))
    if item_type:
        # Soft preference: include item_type in search but do not require it
        q = q.order_by((Part.item_type == item_type).desc(), Part.created_at.desc())
    else:
        q = q.order_by(Part.created_at.desc())

    rows = db.execute(q.limit(limit)).scalars().all()
    return [r.to_dict() for r in rows]


@app.get("/parts/candidates", dependencies=[Depends(require_api_key)])
def parts_candidates(
    name: str,
    internal_reference: Optional[str] = None,
    item_type: Optional[str] = None,
    limit: conint(ge=1, le=200) = 50,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    return _candidate_search(db, name=name, internal_reference=internal_reference, item_type=item_type, limit=int(limit))


@app.post("/parts/candidates/bulk", dependencies=[Depends(require_api_key)])
def parts_candidates_bulk(payload: BulkCandidateQuery, db: Session = Depends(get_db)) -> List[List[Dict[str, Any]]]:
    results: List[List[Dict[str, Any]]] = []
    for q in payload.queries:
        lim = int(min(q.limit or payload.global_limit, 200))
        results.append(_candidate_search(db, name=q.name, internal_reference=q.internal_reference, item_type=q.item_type, limit=lim))
    return results


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


class LastPrefixPayload(BaseModel):
    last_prefix: str = Field(..., min_length=1, max_length=20)

@app.get("/ui/last_prefix", dependencies=[Depends(require_api_key)])
def get_last_prefix(module: str = "DEFAULT", db: Session = Depends(get_db)) -> Dict[str, Any]:
    module = (module or "DEFAULT").strip()
    setting = db.execute(select(UiSetting).where(UiSetting.module == module)).scalar_one_or_none()
    return {"module": module, "last_prefix": setting.last_prefix if setting else None}

@app.put("/ui/last_prefix", dependencies=[Depends(require_api_key)])
def set_last_prefix(payload: LastPrefixPayload, module: str = "DEFAULT", db: Session = Depends(get_db)) -> Dict[str, Any]:
    module = (module or "DEFAULT").strip()
    last_prefix = payload.last_prefix.strip()
    if not last_prefix:
        raise HTTPException(status_code=422, detail="last_prefix cannot be empty")

    setting = db.execute(select(UiSetting).where(UiSetting.module == module)).scalar_one_or_none()
    if setting is None:
        setting = UiSetting(module=module, last_prefix=last_prefix)
        db.add(setting)
    else:
        setting.last_prefix = last_prefix
    db.commit()
    return {"module": module, "last_prefix": last_prefix}

class ItemCategoryPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

@app.get("/ui/item_categories", dependencies=[Depends(require_api_key)])
def list_item_categories(db: Session = Depends(get_db)) -> List[str]:
    rows = db.execute(select(ItemCategory).order_by(ItemCategory.name.asc())).scalars().all()
    return [r.name for r in rows]

@app.post("/ui/item_categories", dependencies=[Depends(require_api_key)])
def add_item_category(payload: ItemCategoryPayload, db: Session = Depends(get_db)) -> Dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be empty")

    existing = db.execute(select(ItemCategory).where(ItemCategory.name == name)).scalar_one_or_none()
    if existing:
        return {"status": "exists", "name": name}

    db.add(ItemCategory(name=name))
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category already exists") from e

    return {"status": "created", "name": name}

@app.delete("/ui/item_categories/{name}", dependencies=[Depends(require_api_key)])
def remove_item_category(name: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    name = name.strip()
    existing = db.execute(select(ItemCategory).where(ItemCategory.name == name)).scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="Category not found")
    db.delete(existing)
    db.commit()
    return {"status": "deleted", "name": name}



@app.post("/admin/reset_counters", dependencies=[Depends(require_api_key)])
def reset_counters(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Dev/admin: reset all prefix counters (next IDs restart from 001 per prefix).

    Protected by X-API-Key. Intended for testing environments.
    """
    with db.begin():
        db.query(IDCounter).delete()
    return {"ok": True}
