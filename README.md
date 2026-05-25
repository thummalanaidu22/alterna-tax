# AI Property Intelligence System

Automated property due diligence platform powered by MiniCPM-V vision AI and deterministic SOP rule engine.

## Architecture

```
Frontend (React + Vite + TailwindCSS)
    ↓ HTTP / Proxy
FastAPI Backend
    ↓
Pipeline Orchestrator
    ├── GIS Service          → Regrid / County GIS parcel boundaries
    ├── Satellite Renderer   → Google Maps Static API + Pillow overlay
    ├── Street Capture       → Google Street View Static API (3 angles)
    ├── Vision Service       → MiniCPM-V via Ollama
    ├── Rule Engine          → Deterministic SOP evaluation
    └── Report Generator     → JSON + HTML reports
```

## Quick Start

### Prerequisites

- Docker + Docker Compose  
- Ollama running with `minicpm-v` model  
- Google Maps API key (optional — placeholders used without it)

### 1. Clone & configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 2. Pull AI model

```bash
ollama pull minicpm-v
```

### 3. Start with Docker Compose

```bash
docker compose up --build
```

- Frontend: http://localhost:3000  
- Backend API: http://localhost:8000  
- API Docs: http://localhost:8000/docs  

### 4. Local development (without Docker)

**Backend:**
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/properties/analyze` | Submit single property |
| POST | `/api/properties/batch` | Submit batch |
| GET | `/api/properties/jobs` | List all jobs |
| GET | `/api/properties/jobs/{id}` | Get job status + result |
| GET | `/api/properties/batch/{id}` | Get batch status |
| GET | `/api/health` | System health check |

### Analyze a property

```bash
curl -X POST http://localhost:8000/api/properties/analyze \
  -H "Content-Type: application/json" \
  -d '{"latitude": 25.7617, "longitude": -80.1918, "property_id": "PROP-001"}'
```

## Pipeline Stages

1. **GIS Fetch** — Queries Regrid API for parcel boundary. Falls back to estimated 100×100ft parcel.
2. **Satellite Capture** — Downloads satellite tile, composites red parcel boundary overlay + marker.
3. **Street Capture** — Downloads 3 Street View angles (center 0°, right 90°, left 270°).
4. **Vision Analysis** — Sends all 4 images to MiniCPM-V with structured SOP prompt.
5. **Rule Engine** — Applies deterministic SOP rules to AI observations. Overrides AI if rules trigger.
6. **Report Generation** — Writes JSON and HTML reports to `data/reports/`.

## Decision Logic

| Property Type | Reject If |
|--------------|-----------|
| Residential | Boarded windows, roof damage, abandoned, burned, heavy debris |
| Commercial | Hospital, church, mosque, synagogue, gas station, auto repair |
| Vacant Land | Narrow/triangle lot, landlocked, no road access, heavily wooded |
| Agriculture | Isolated pond + irregular shape, no road access |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, TailwindCSS v4, Redux Toolkit, TanStack Query |
| Backend | Python 3.11, FastAPI, Pydantic v2, AsyncIO |
| Image Processing | Pillow, OpenCV |
| GIS | GeoPandas, Shapely, PyProj |
| AI | Ollama + MiniCPM-V |
| Maps | Google Maps Static API, Street View API |
| Browser Automation | Playwright (fallback) |
