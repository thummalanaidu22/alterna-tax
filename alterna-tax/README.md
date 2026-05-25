# AI Property Intelligence System

Automated property due diligence platform powered by **MiniCPM-V** vision AI, a deterministic SOP rule engine, SQLite persistence, real-time WebSocket updates, and a human review workflow.

---

## Table of Contents

- [Architecture](#architecture)
- [What's New in v2.0](#whats-new-in-v20)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Pipeline Stages](#pipeline-stages)
- [Decision Logic](#decision-logic)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)

---

## Architecture

```
Frontend (React 19 + Vite + TailwindCSS v4)
    ↓ HTTP + WebSocket
FastAPI Backend (Python 3.12)
    ↓
Pipeline Orchestrator
    ├── GIS Service          → Regrid / FCC parcel boundaries
    ├── Satellite Renderer   → ESRI World Imagery tiles + Pillow overlay
    ├── Street Capture       → Playwright + Google Maps (3 angles)
    ├── Vision Service       → MiniCPM-V via Ollama (+ second-pass)
    ├── Rule Engine          → Deterministic SOP evaluation
    └── Report Generator     → JSON + HTML reports
    ↓
SQLite (data/propintel.db)   → Persistent job + batch storage
WebSocket Manager            → Real-time pipeline updates to browser
```

---

## What's New in v2.0

### Configurable Vision Model
- Active model: **MiniCPM-V** — purpose-built for multi-image scene analysis, optimised for Apple Silicon, fully stable on Ollama
- Model is set via `OLLAMA_MODEL` in `backend/.env` — swap without any code changes
- `health.py` reports the active model name dynamically
- Alternative: set `OLLAMA_MODEL=qwen2-vl:7b` for stronger OCR/reasoning (requires 16 GB+ RAM, reduce `MAX_CONCURRENT_JOBS` to 3)

### Second-Pass Vision Analysis
- When the model's confidence is below **65%**, the pipeline automatically runs a focused second analysis at `temperature=0` (deterministic)
- Results are **merged conservatively** — risk flags (damage, boarded windows, debris) are kept if *either* pass flagged them; access/structure flags are only cleared if *both* passes agreed
- Final confidence is averaged across both passes

### SQLite Persistence
- All jobs and batches are persisted to `data/propintel.db` via `aiosqlite`
- Jobs survive server restarts — no data loss on reboot
- On startup the orchestrator restores up to 500 recent jobs from the database
- New dependency: `aiosqlite==0.21.0`

### WebSocket Real-Time Updates
- New endpoint: `ws://localhost:8000/api/ws/jobs/{job_id}`
- Every pipeline stage update is pushed to connected clients instantly
- Frontend `createJobWebSocket()` helper in `services/api.ts`
- Polling is still available as fallback

### Human Review Queue
- New backend endpoints:
  - `GET  /api/properties/review-queue` — lists jobs with `NEEDS_HUMAN_REVIEW` that have no verdict yet
  - `PUT  /api/properties/jobs/{id}/review` — submit `approved` / `rejected` verdict with optional notes
- New **Review Queue** page in the frontend with expand/collapse per job, notes field, and one-click approve/reject
- Verdicts are recorded in SQLite (`human_verdict`, `reviewer_notes` columns)

### Property Map
- New **Map** page powered by Mapbox GL JS
- All completed properties shown as color-coded pins: green = Approved, red = Rejected, amber = Needs Review
- Click any pin to see decision, property type, confidence, rejection reasons, and summary in a side panel
- Requires `VITE_MAPBOX_TOKEN` in `frontend/.env.local` (free tier at mapbox.com)

### Vacancy Flag Fix
- `vacancy_signs` now only triggers a review flag when corroborated by at least one additional signal (no structure, abandoned appearance, or debris)
- Prevents false review flags on properties where the model incorrectly flags empty streets as vacancy evidence

### Batch Data Persistence
- Batch form rows and active batch ID are saved to `sessionStorage`
- Data survives page navigation and browser refresh
- Clears automatically when the tab is closed

---

## Quick Start

### Prerequisites

- Python 3.12
- Node.js 18+
- [Ollama](https://ollama.com) installed and running

### 1. Clone and configure

```bash
# Backend
cd alterna-tax/backend
cp .env.example .env          # then edit .env with your keys

# Frontend
cd alterna-tax/frontend
cp .env.example .env.local    # then edit .env.local
```

### 2. Pull the AI model

```bash
ollama pull minicpm-v
```

> To use the heavier Qwen2-VL model instead: `ollama pull qwen2-vl:7b` and set `OLLAMA_MODEL=qwen2-vl:7b` in `backend/.env`. Reduce `MAX_CONCURRENT_JOBS` to 3 on machines with less than 16 GB RAM.

### 3. Backend setup

```bash
cd alterna-tax/backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Wait for:
```
INFO: SQLite database ready at data/propintel.db
INFO: Application startup complete.
```

### 4. Frontend setup

Open a new terminal:

```bash
cd alterna-tax/frontend
npm install
npm run dev
```

Open **http://localhost:5173**

### 5. Verify

```bash
curl -H "X-API-Key: change_me_to_a_strong_random_secret" \
     http://localhost:8000/api/health
```

Expected:
```json
{"status":"ok","version":"1.0.0","ollama":"connected","model":"minicpm-v"}
```

---

## Environment Variables

### `backend/.env`

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | *(required)* | Secret key — all `/api/properties/*` requests must send `X-API-Key` header. Leave blank to disable auth in dev. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `minicpm-v` | Vision model name — must be pulled via `ollama pull` |
| `OLLAMA_TIMEOUT` | `120` | Seconds before vision call times out |
| `GOOGLE_MAPS_API_KEY` | — | Required for Street View capture |
| `ARCGIS_API_KEY` | — | Optional — improves parcel boundary accuracy |
| `MAX_CONCURRENT_JOBS` | `5` | Parallel pipeline slots. Use 3 for 7B model on <16 GB RAM. |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | Allowed frontend origins |
| `DEBUG` | `false` | Enable FastAPI debug mode |

### `frontend/.env.local`

| Variable | Description |
|---|---|
| `VITE_API_KEY` | Must match `API_KEY` in `backend/.env` |
| `VITE_MAPBOX_TOKEN` | Mapbox public token for the Map page — get free at mapbox.com |

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/properties/analyze` | Submit a single property for analysis |
| `POST` | `/api/properties/batch` | Submit a batch (max 500 properties) |
| `GET` | `/api/properties/jobs` | List recent jobs (persisted across restarts) |
| `GET` | `/api/properties/jobs/{id}` | Get job status and full result |
| `PUT` | `/api/properties/jobs/{id}/review` | Submit human verdict for a review-flagged job |
| `GET` | `/api/properties/review-queue` | List jobs awaiting human review |
| `GET` | `/api/properties/batch/{id}` | Get aggregated batch status |
| `GET` | `/api/health` | System health — Ollama status, model name, version |
| `WS` | `/api/ws/jobs/{id}` | WebSocket — real-time pipeline stage updates |

### Analyze a single property

```bash
curl -X POST http://localhost:8000/api/properties/analyze \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change_me_to_a_strong_random_secret" \
  -d '{"latitude": 25.7617, "longitude": -80.1918, "property_id": "PROP-001"}'
```

### Submit a human verdict

```bash
curl -X PUT http://localhost:8000/api/properties/jobs/{job_id}/review \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change_me_to_a_strong_random_secret" \
  -d '{"verdict": "approved", "notes": "Verified in person — structure is sound"}'
```

### Connect via WebSocket

```js
const ws = new WebSocket("ws://localhost:8000/api/ws/jobs/<job_id>");
ws.onmessage = (e) => console.log(JSON.parse(e.data)); // receives PropertyJob on every stage
```

---

## Pipeline Stages

| # | Stage | What it does |
|---|---|---|
| 1 | **GIS Fetch** | Queries Regrid API for parcel polygon, area, and zoning. Falls back to FCC Area API, then estimated 100×100 ft parcel. |
| 2 | **Satellite Capture** | Downloads ESRI World Imagery tiles at zoom 19 (~0.3 m/px), stitches a 5×5 grid, composites red parcel boundary + center pin. |
| 3 | **Street Capture** | Playwright headless browser captures 3 Google Maps Street View angles (left −35°, center, right +35°). Tries ±100 m / ±200 m offsets if no coverage. |
| 4 | **Vision Analysis** | Sends up to 4 images to MiniCPM-V via Ollama with a structured SOP prompt (temperature 0.1). If confidence < 65%, runs a second focused pass (temperature 0) and merges results conservatively. |
| 5 | **Rule Engine** | Applies deterministic SOP rules. AI observations can trigger reject/review but cannot override hard SOP rules. |
| 6 | **Report Generation** | Writes `data/reports/{job_id}.json` and `data/reports/{job_id}.html`. Accessible at `/reports/{job_id}.html`. |

Stages 1–3 run **in parallel**. Stages 4–6 are sequential.

---

## Decision Logic

| Property Type | Reject if any… | Review if… |
|---|---|---|
| **Residential** | Boarded windows (plywood), fire/burn damage, structural collapse, active construction, heavy trash | Mobile home detected, no structure on parcel, corroborated vacancy signs |
| **Mobile Home** | Same as residential | Always routes to underwriter review |
| **Commercial** | Hospital, K-12 school, church/mosque/synagogue/temple, gas station, auto repair, parking-lot-only parcel | — |
| **Industrial** | — | Always routes to underwriter review |
| **Vacant Land** | Narrow/triangle lot, no street frontage, landlocked, heavily wooded, parcel undersized vs. neighbors | — |
| **Agriculture** | Water hole inside parcel, landlocked, triangle/narrow shape | Missing house, no road frontage, or irregular shape (all 3 required for approve) |
| **All types** | — | No aerial image available, confidence < 65%, GIS/vision mismatch |

---

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Frontend framework | React | 19 |
| Frontend build | Vite | 8 |
| Styling | TailwindCSS | v4 |
| State management | Redux Toolkit + TanStack Query | — |
| Maps | Mapbox GL JS | 3 |
| Backend framework | FastAPI | 0.115 |
| Backend runtime | Python | 3.12 |
| Data validation | Pydantic v2 | 2.10 |
| Vision AI | MiniCPM-V via Ollama | — |
| Image processing | Pillow, OpenCV | — |
| GIS | GeoPandas, Shapely, PyProj | — |
| Database | SQLite via aiosqlite | — |
| Browser automation | Playwright | 1.49 |
| Real-time | FastAPI WebSocket | — |

---

## Project Structure

```
alterna-tax/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app, lifespan, WebSocket endpoint
│   │   ├── config.py                # Pydantic settings (reads .env)
│   │   ├── auth.py                  # X-API-Key header guard
│   │   ├── db.py                    # SQLite persistence (aiosqlite)
│   │   ├── ws_manager.py            # WebSocket connection manager
│   │   ├── models/
│   │   │   └── property.py          # All Pydantic models and enums
│   │   ├── pipeline/
│   │   │   └── orchestrator.py      # Pipeline coordinator + job lifecycle
│   │   ├── routers/
│   │   │   ├── properties.py        # Property endpoints + review endpoints
│   │   │   └── health.py            # Health check
│   │   └── services/
│   │       ├── vision_service.py    # MiniCPM-V via Ollama + second-pass logic
│   │       ├── rule_engine.py       # Deterministic SOP rules
│   │       ├── gis_service.py       # Parcel boundary fetch
│   │       ├── satellite_service.py # ESRI tile stitching
│   │       ├── street_capture_service.py  # Playwright Street View
│   │       └── report_generator.py  # JSON + HTML report writer
│   ├── data/
│   │   ├── propintel.db             # SQLite database (auto-created)
│   │   ├── images/                  # Captured satellite + street images
│   │   └── reports/                 # Generated JSON + HTML reports
│   ├── requirements.txt
│   └── .env
├── frontend/
│   ├── src/
│   │   ├── App.tsx                  # Routes: Dashboard, Analyze, Batch, Jobs, Map, Review
│   │   ├── pages/
│   │   │   ├── DashboardPage.tsx    # System stats + recent jobs
│   │   │   ├── AnalyzePage.tsx      # Single property submission + live pipeline
│   │   │   ├── BatchPage.tsx        # CSV import + batch submission (session-persistent)
│   │   │   ├── JobsPage.tsx         # All jobs list + detail view
│   │   │   ├── MapPage.tsx          # Mapbox map with decision-colored pins
│   │   │   └── ReviewPage.tsx       # Human review queue with verdict workflow
│   │   ├── components/
│   │   │   ├── layout/              # Sidebar + Layout
│   │   │   ├── property/            # AnalysisResultCard, PipelineProgress, ImageGallery
│   │   │   └── ui/                  # Card, Spinner, StatusBadge, DecisionBadge
│   │   ├── services/
│   │   │   └── api.ts               # Axios client + WebSocket helper
│   │   ├── store/
│   │   │   ├── index.ts             # Redux store + sessionStorage subscriber
│   │   │   └── jobsSlice.ts         # Batch rows persisted to sessionStorage
│   │   └── types/
│   │       └── property.ts          # All TypeScript types
│   ├── .env.local                   # VITE_API_KEY + VITE_MAPBOX_TOKEN
│   └── package.json
├── docker-compose.yml
├── Caddyfile
└── README.md
```

---

## Changelog

### v2.0.0
- Vision model made fully configurable via OLLAMA_MODEL env var (default: minicpm-v)
- Added automatic second-pass analysis for low-confidence results
- Added SQLite persistence — jobs survive server restarts
- Added WebSocket endpoint for real-time pipeline updates
- Added human review queue (frontend + backend)
- Added Mapbox property map page
- Fixed vacancy flag over-triggering — now requires corroborating signals
- Batch form data now persists via sessionStorage (survives navigation + refresh)
- Health endpoint reports active model name dynamically

### v1.0.0
- Initial release with MiniCPM-V, in-memory job storage, batch CSV upload
