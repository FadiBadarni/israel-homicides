# Crime Pipeline — Case Browser UI

Two-process setup: FastAPI backend + Next.js frontend.

## Quick start

### 1. Backend (FastAPI)

```bash
# from repo root
pip install fastapi uvicorn

uvicorn ui.api.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 2. Frontend (Next.js)

```bash
cd ui/frontend
npm install
npm run dev
```

Open: http://localhost:3000

## Features

- Sortable/filterable cases table (TanStack Table)
- Filters: city, outcome, weapon, review status, confidence slider, date range, name search, flagged toggle
- RTL-safe victim names via `<bdi dir="auto">`
- Confidence badge: green ≥85%, amber 65-84%, red <65%; ⚠ when flagged
- 3-column case detail: victim card | confidence + sources | media gallery
- Lightbox for media items

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/runs | All run metadata |
| GET | /api/cases | Paginated case list (filterable) |
| GET | /api/cases/{run_id}/{case_index} | Full case detail |
| GET | /api/filters | Available filter values |
| GET | /api/stats | Aggregate stats |
| GET | /health | Health check |
