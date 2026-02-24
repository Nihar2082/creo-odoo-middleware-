# Parts Backend (FastAPI + PostgreSQL)

This backend provides:
- **Centralized External ID reservation** (race-safe)
- **Parts storage** with fixed columns + **JSONB** for dynamic/custom columns
- Simple **API key** authentication

## Prerequisites
- Docker Desktop installed (Windows/Mac) or Docker Engine (Linux)
- `docker compose` available

## Quick start (local)
1. Create your environment file:
   - Copy `.env.example` to `.env`
   - Set `API_KEY` to something secure

2. Start services:
```bash
docker compose up --build
```

3. Open API docs:
- Swagger UI: `http://localhost:8000/docs`

## Authentication
All endpoints require an API key header:
- `X-API-Key: <your key>`

## Core endpoints

### Reserve External IDs
`POST /ids/reserve`
```json
{ "prefix": "PS", "count": 5 }
```
Response:
```json
{ "ids": ["PS_000001", "PS_000002", "PS_000003", "PS_000004", "PS_000005"] }
```

### Bulk upsert parts
`POST /parts/bulk_upsert`
```json
[
  {
    "external_id": "PS_000123",
    "part_name": "Bracket",
    "internal_reference": "PS_000123",
    "item_type": "Bought Part",
    "qty": 2,
    "status": "New",
    "data": {"Material": "Al6061", "Supplier": "ABC GmbH"},
    "created_by": "vishakha"
  }
]
```

## Notes for production
- Put this behind HTTPS (nginx / company reverse proxy)
- Use a strong `API_KEY`
- Restrict network access to internal LAN/VPN
- Enable backups for PostgreSQL (IT usually handles this)
