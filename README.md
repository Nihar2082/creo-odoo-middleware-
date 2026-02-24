# SCIPRIOS Middleware - EBOM to Odoo Integration

Complete middleware solution for processing Creo EBOM files and preparing Odoo-ready imports with intelligent part matching and database management.

## Project Structure

```
backend_server/    - FastAPI PostgreSQL backend (Docker support)
ui_client/         - PySide6 desktop application
README.md          - This file
```

## Quick Start

### Backend Setup

1. Navigate to backend_server
2. Create .env file from .env.example and set credentials
3. Start with Docker:

```bash
cd backend_server
docker compose up --build
```

Backend runs at: http://localhost:8000

### UI Client Setup

1. Navigate to ui_client
2. Install dependencies:

```bash
cd ui_client
python -m pip install -r requirements.txt
```

3. Create config.json from config.json.example
4. Run the application:

```bash
python -m ui_pyside.main
```

## Features

- EBOM file parsing (.txt, .csv)
- Smart part matching (80% similarity threshold)
- Sequential ID generation (PS_000001, STD_000002, etc.)
- PostgreSQL backend with REST API
- PySide6 GUI application
- Database cleanup utilities
- Docker containerization

## Configuration

### UI Client (ui_client/config.json.example)

```json
{
  "api_url": "http://localhost:8000",
  "api_key": "your-api-key-here"
}
```

### Backend (backend_server/.env.example)

```
POSTGRES_USER=your_db_user
POSTGRES_PASSWORD=your_db_password
POSTGRES_DB=parts_database
API_KEY=your-api-key-here
API_PORT=8000
```

## Database Management

### Cleanup Database

Safely manage database records:

```bash
cd ui_client
python cleanup_database.py
```

Options:
- Delete by number range
- Delete entire prefix
- Delete single entry
- Interactive selection

## Usage Workflow

1. Load EBOM file in UI
2. Assign part prefixes (PS, MD, STD, etc.)
3. Review detected existing parts
4. Export as Odoo-ready CSV
5. Use cleanup_database.py to manage test data

## API Documentation

When backend is running, access Swagger UI:
http://localhost:8000/docs

## System Requirements

- Python 3.9+
- PostgreSQL
- Docker & Docker Compose
- 2GB RAM minimum
- 500MB disk space

## Installation

1. Clone repository
2. Copy and edit configuration files (.env.example, config.json.example)
3. Follow Backend and UI Client setup steps above

## Testing

Backend API tests:
```bash
cd backend_server
python -m pytest
```

UI functionality - test through GUI after startup.

## Project Status

Production-ready with full PostgreSQL backend, matching logic, and cleanup utilities.

## Support

For issues or questions about setup, refer to configuration examples in each directory.
