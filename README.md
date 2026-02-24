# SCIPRIOS Middleware – Combined Package (v2)

This package contains:
- `backend_server/` (FastAPI + PostgreSQL via Docker)
- `ui_client/` (PySide UI)

## Backend
```powershell
cd backend_server
# set API_KEY in .env
docker compose up --build
```

Open Swagger: http://localhost:8000/docs

## UI
```powershell
cd ui_client
python -m pip install -r requirements.txt
python -m ui_pyside.main
```

## DB Viewer (Edit/Delete)
In the UI, click **DB Viewer** to:
- View records stored in PostgreSQL
- Edit cells directly
- Click **Save to DB** to persist changes (only on button click)
- Select rows and click **Delete Selected (Permanent)** to hard-delete records
