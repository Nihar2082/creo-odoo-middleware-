# SCIPRIOS Middleware: Creo → Odoo Integration

A PySide6 desktop application for processing Creo EBOM part lists and preparing Odoo-ready imports with intelligent part matching and sequential ID generation.

## Features

- **EBOM Processing**: Import and parse Creo EBOM files (.txt, .csv)
- **Smart Part Matching**: Detect existing parts with 80%+ similarity threshold
- **Sequential ID Generation**: Auto-generate unique External IDs (PS_000001, STD_000002, etc.)
- **PostgreSQL Backend**: Centralized part registry and storage
- **PySide6 GUI**: User-friendly desktop interface
- **Database Cleanup Tool**: Safely delete or manage test data

## Project Structure

```
├── backend/                    # Core services and database layer
│   ├── db/                     # Database layer (repo.py, schema.py)
│   ├── parsers/                # EBOM file parsers
│   ├── services/               # ID generation, pipeline
│   └── export/                 # Odoo export formatting
├── matching_logic/             # Part matching engine
│   ├── core/                   # Match algorithm, normalization
│   └── models/                 # Type definitions
├── ui_pyside/                  # Desktop GUI (PySide6)
├── cleanup_database.py         # Database management utility
├── requirements.txt            # Python dependencies
├── config.json.example         # Configuration template
└── README.md                   # This file
```

## Quick Start

### Prerequisites
- Python 3.9+
- PostgreSQL backend running at `http://localhost:8000`
- Valid API key

### Setup

1. **Clone or extract repository**
   ```bash
   cd ui_client
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate      # Linux/Mac
   .venv\Scripts\activate         # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure API credentials**
   ```bash
   cp config.json.example config.json
   # Edit config.json with your API URL and key
   ```

5. **Run the application**
   ```bash
   python -m ui_pyside.main
   ```

## Usage

### Main Workflow

1. **Load EBOM**: Import a Creo EBOM file
2. **Select Prefix**: Assign prefix (PS, MD, STD, etc.)
3. **Review Matches**: Check for detected existing parts
4. **Export**: Generate Odoo-ready CSV

### ID Generation Rules

- **Designer-provided prefix** (PS, MD, HW) → formats as `PS_000001`
- **Standard parts** → always use `STD_` prefix regardless of input
- **Counter persistence** → Only increment when exported (prevents re-use)
- **Unique tracking** → Counter continues from last export, preventing collisions

## Database Management

### Cleanup Tool

Safely delete old test data:

```bash
python cleanup_database.py
```

**Options:**
- Delete by range (PS_000001 to PS_000100)
- Delete entire prefix (all PS_* parts)
- Delete single part (PS_000460)
- Interactive browser and selection
- Cancel without changes

**Safety features:**
- ✅ Preview before deletion
- ✅ Requires "DELETE" confirmation
- ✅ No automatic deletions
- ✅ Counter not reset (next ID continues sequentially)

**Example:**
```
Delete specific range:
Prefix: PS
Start: 467
End: 476
→ PS_000477 created next
```

## Configuration

Create `config.json` from template:

```json
{
  "api_url": "http://localhost:8000",
  "api_key": "your-api-key-here"
}
```

⚠️ **Never commit `config.json`** — use `config.json.example` as template and add real credentials locally.

## Architecture

### Matching Engine
- Normalize part names (remove prefixes, uppercase)
- String similarity detection (80% threshold)
- Status markers: NEW, POSSIBLE_MATCH, EXISTING
- Backend-driven registry from PostgreSQL

### Database Layer
- **PostgreSQL**: Central source of truth for all parts
- **SQLite**: Local settings only (prefixes, counters)
- **Repo class**: Handles all backend communication
- **Session persistence**: Counters survive restarts

### Export Format
Odoo-compatible CSV with:
- **External ID**: `PS_000001` (unique)
- **Part Name**: UPPERCASE standardized
- **Internal Reference**: Same as External ID
- **Type of Item**: Part category

## Troubleshooting

### API Connection Failed
Check backend is running:
```bash
curl http://localhost:8000/health
```
Verify `config.json` credentials.

### No Parts Matched
Existing parts must be in database from previous exports. Re-upload EBOM with same prefix for matching.

### Counter Issues
Local and backend counters are independent. Use cleanup tool to reset backend if needed:
```bash
python cleanup_database.py
```

## Dependencies

- **PySide6**: Qt-based GUI
- **requests**: HTTP API client
- **SQLite3**: Built-in for local storage

See `requirements.txt` for full list.

## Status

✅ Production-ready  
✅ PostgreSQL-only backend  
✅ Smart part matching with 80% threshold  
✅ Cleanup utilities included  

---

**Built by**: Nihar Patel @ SCIPRIOS  
**License**: Proprietary

