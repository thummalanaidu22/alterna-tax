# Alterna Property Intelligence — Architecture & Design Decisions

## Overview

Alterna Property Intelligence is an automated property due-diligence platform built for the **Alterna Tax Certificate Fund**. It accepts a list of property coordinates (latitude/longitude), runs a full analysis pipeline, and outputs one of three decisions per property:

| Decision | Meaning |
|---|---|
| `APPROVED` | Property passes all SOP criteria — safe to proceed |
| `REJECTED` | Property fails one or more hard SOP rules — do not invest |
| `NEEDS_HUMAN_REVIEW` | Ambiguous — send to underwriter for manual review |

The system processes 5 properties concurrently and completes a full analysis per property in approximately 30–90 seconds (dominated by Playwright browser automation).

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (React + Vite)                  │
│  BatchPage.tsx  →  Redux Toolkit store  →  FastAPI REST API     │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP (JSON)
┌───────────────────────────────▼─────────────────────────────────┐
│                   BACKEND (FastAPI / Python 3.11)               │
│                                                                 │
│   POST /api/v1/properties/batch                                 │
│           │                                                     │
│           ▼                                                     │
│   PipelineOrchestrator  (asyncio.Semaphore(5))                  │
│           │                                                     │
│   ┌───────┴──────────────────────────┐                          │
│   │  asyncio.gather() — PARALLEL     │                          │
│   │  ┌──────────┐ ┌────────────────┐ ┌──────────────────────┐  │
│   │  │  Stage 1 │ │    Stage 2     │ │       Stage 3        │  │
│   │  │  GIS     │ │  Satellite     │ │   Street View        │  │
│   │  │  Fetch   │ │  Capture       │ │   Capture            │  │
│   │  └──────────┘ └────────────────┘ └──────────────────────┘  │
│   └───────────────────────────────────────────────────────────  │
│           │                                                     │
│           ▼  (Stage 4 — sequential, needs images from 1-3)     │
│   Vision Analysis (Ollama / MiniCPM-V multimodal)              │
│           │                                                     │
│           ▼  (Stage 5 — sequential, needs vision output)       │
│   Rule Engine (pure Python deterministic SOP)                  │
│           │                                                     │
│           ▼  (Stage 6)                                         │
│   Report Generator (JSON + PDF)                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

### Backend

| Component | Technology | Why Chosen | Why Not X |
|---|---|---|---|
| Framework | **FastAPI** | Native async support; auto-generated OpenAPI docs; Pydantic validation | Django is sync-first (bad for Playwright); Flask lacks async DI |
| Python version | **3.11** | `asyncio.gather` + `asyncio.to_thread` work reliably; no breaking changes vs 3.12 | 3.12 changed some internals that affected httpx/playwright at time of build |
| Data validation | **Pydantic v2** | 10× faster than v1; strict enum validation prevents silent type bugs | Marshmallow has no native FastAPI integration |
| Settings | **pydantic-settings** | `.env` parsing with type coercion for free | python-dotenv alone gives raw strings only |
| HTTP client | **httpx** | Native async; used for ESRI tiles + OSM Overpass API | requests is sync only; aiohttp has worse API ergonomics |
| Browser automation | **Playwright** | Can intercept WebGL canvas; modern async Python API; headless Chromium | Selenium has no async API; Puppeteer is Node.js; Playwright has best Google Maps compatibility |
| Computer vision | **OpenCV + NumPy + Pillow** | cv2 for marker overlay; numpy for pixel brightness check; PIL for image resize/format | All standard; no viable alternatives at this price point |
| Vision AI | **Ollama / MiniCPM-V** | Runs locally (no API cost per call); MiniCPM-V is specifically trained on multi-image reasoning | See AI section below |
| Concurrency limit | **asyncio.Semaphore(5)** | Hard ceiling on browser instances to prevent RAM exhaustion | Thread pool would block event loop during I/O |

### Frontend

| Component | Technology | Why Chosen |
|---|---|---|
| Framework | **React 18 + TypeScript** | Industry standard; StrictMode helps catch side effects |
| Build tool | **Vite** | ~10× faster HMR than CRA/Webpack |
| State management | **Redux Toolkit** | Predictable state for async batch jobs with multiple status states |
| Styling | **TailwindCSS** | Utility-first; no CSS file maintenance overhead |
| Routing | **React Router v6** | Standard; no alternative needed |

---

## Pipeline Stage Details

### Stage 1 — GIS Fetch

**What it does:** Queries a GIS / parcel database (configured via `ARCGIS_API_KEY`) to retrieve parcel metadata: zoning type, lot size (sq ft), parcel shape polygon, and address.

**Why parallel with stages 2–3:** GIS data is purely informational — the satellite and street captures need only `(lat, lon)` which we already have. Running all three in parallel cuts 60–90 seconds off the wall-clock time.

**Fallback:** If GIS fails, the pipeline continues with `parcel = {}`. The rule engine uses the vision model's property type guess instead.

---

### Stage 2 — Satellite Capture

**What it does:** Fetches a real aerial/satellite image of the property.

**Image source: ESRI World Imagery tiles (free, no API key)**

Why ESRI over alternatives:

| Source | Cost | Resolution | Chosen? |
|---|---|---|---|
| **ESRI World Imagery** | Free, no key | High (sub-meter in USA) | ✅ Yes |
| Google Maps Static API | $2–7 / 1000 calls | High | ❌ Cost at scale |
| Mapbox Satellite | Free tier only 50k/mo | High | ❌ Rate limits |
| Bing Maps | Free with key | Medium | ❌ Lower resolution |

**How it works:**
1. Computes the fractional tile coordinates for the property at `zoom=19`
2. Stitches a 5×5 grid of 256px tiles → 1280×1280 canvas
3. Crops to 800×800 centred on the property
4. Queries OSM Overpass API for the building footprint polygon
5. Draws a red boundary + red pin marker
6. Saves as JPEG (quality 85)

**Why OSM for polygon over GIS parcel boundary:** The GIS parcel bounding box is a large rectangle (50m+ each side) covering the entire lot. The OSM building footprint is the actual structure outline — much tighter, so the red boundary in the aerial image correctly highlights the building, not the whole lot.

---

### Stage 3 — Street View Capture

**What it does:** Opens a headless Chromium browser, navigates to Google Maps Street View for the property, captures three frames: LEFT (−35°), CENTER (on-axis bearing), RIGHT (+35°).

**Why Playwright + Google Maps (not Street View Static API):**

| Option | Cost | Quality | Chosen? |
|---|---|---|---|
| **Playwright + Google Maps** | Free (browser automation) | Full 1280×720 panorama | ✅ Yes |
| Street View Static API | $7 / 1000 calls | 640×640, no panorama rotation | ❌ Cost + limited |
| Mapillary | Free | Spotty US coverage | ❌ Coverage gaps |

**Bearing calculation:** We compute the compass bearing from the street camera position to the building centroid (from OSM). This ensures all three frames are pointed at the correct building, not at a random angle.

**No-coverage detection (black screen fix):**
- Rural properties and parcels on private roads often have no Street View photography
- Google Maps renders a **black WebGL canvas** for these (the canvas DOM element exists but shows nothing)
- The old code reported these as `ok=3/3` and saved 22–32 KB black JPEG files
- **Fix:** After the page loads, we take a screenshot and measure the mean pixel brightness of the center 50% of the frame. If brightness < 15 (0–255 scale), the screen is black → no coverage
- **Fallback search:** We try 8 nearby offsets (±100m and ±200m in N/S/E/W) on the same browser page to find the nearest road with actual photography
- If no coverage found within ~200m, all three views are skipped cleanly — the vision model handles `street_paths={}` gracefully

**UI cleanup:** We hide Google Maps navigation controls, search bar, watermark, and info side-panel via CSS injection so the panorama canvas fills the full 1280×720 frame.

---

### Stage 4 — Vision Analysis

**What it does:** Sends up to 4 images (1 satellite + up to 3 street views) to a multimodal AI model and asks it to return structured JSON describing the property.

**AI model: MiniCPM-V via Ollama**

| Model | Size | Multi-image | Property accuracy | Chosen? |
|---|---|---|---|---|
| **MiniCPM-V (3B)** | ~4 GB | ✅ Yes (up to 4) | High — trained on dense visual reasoning | ✅ Yes |
| LLaVA 7B | ~8 GB | Limited (1 image) | Low — confuses hurricane shutters with plywood | ❌ |
| GPT-4o | Cloud | ✅ Yes | Very high | ❌ $0.01+/call, GDPR concerns |
| Claude 3 Opus | Cloud | ✅ Yes | Very high | ❌ Same cost/privacy concerns |

**Why local (Ollama) over cloud AI:**
- No per-call cost — critical for bulk batch processing
- No data privacy issues (property coordinates never leave the network)
- Deterministic — same image gives same result (no temperature drift with cloud APIs)
- Offline capable — works without internet after model download

**Prompt design:** The prompt is a single structured string that:
1. Labels each image by position (aerial = Image 1, street views = Images 2–4)
2. Defines every output field with precise rules (e.g., `plywood_on_windows: true ONLY for raw wood boards, not hurricane shutters, not blinds`)
3. Returns raw JSON only (no markdown fences, no explanation text)
4. Uses `temperature=0.1` for near-deterministic output

**Resilience:** If Ollama is unreachable (e.g., not running), the service returns `None` and the rule engine falls back to GIS-only data with a forced `NEEDS_HUMAN_REVIEW`.

---

### Stage 5 — Rule Engine

**What it does:** Takes the vision JSON + GIS parcel metadata and applies a deterministic decision tree matching the Alterna SOP exactly.

**Why deterministic rules over ML classifier:**
- SOP rules are explicit and auditable — every rejection has a named reason
- No training data needed
- Output is explainable to underwriters and investors
- Rules can be updated immediately when SOP changes without retraining

**Decision logic (simplified):**

```
if no aerial image → REVIEW (cannot automate without visual)
if confidence < 65% → REVIEW (too uncertain)

BANNED FACILITIES (reject regardless of type):
  hospital / K-12 school / church / mosque / synagogue / temple
  gas station / auto repair shop

RESIDENTIAL:
  REJECT if: plywood boards / fire damage / structural damage / roof damage
             heavy trash/debris / active construction
  REVIEW if: mobile home / no structure visible

COMMERCIAL:
  REJECT if: parking lot only (no building)

VACANT LAND:
  REJECT if: narrow/triangle lot / no street frontage / side lot
             landlocked / heavily wooded / undersized vs neighbors

AGRICULTURE:
  REJECT if: water hole on land / landlocked / triangle lot
  APPROVE only if ALL: house on parcel + fronts a road + regular shape

Otherwise → APPROVED
```

**Confidence adjustment:** If GIS data says "residential" but vision says "commercial", we reduce confidence by 15% to flag the mismatch for review.

---

### Stage 6 — Report Generator

**What it does:** Produces a structured JSON report per property (and optionally PDF) with the decision, all observations, rejection/review reasons, and image references.

---

## Data Flow

```
CSV upload (lat,lon pairs)
       │
       ▼
BatchPage.tsx → POST /api/v1/properties/batch
       │
       ▼
PipelineOrchestrator.submit_batch()
  - deduplicates by (lat,lon) rounded to 6 decimal places
  - creates PropertyJob per unique coordinate
  - launches asyncio task per job
       │
       ▼  (per job, max 5 concurrent via Semaphore)
asyncio.gather(GIS, Satellite, StreetView)  ← PARALLEL
       │
       ▼
Vision Analysis  ← SEQUENTIAL (needs images)
       │
       ▼
Rule Engine  ← SEQUENTIAL (needs vision output + parcel metadata)
       │
       ▼
Report  ← SEQUENTIAL
       │
       ▼
PropertyJob.status = COMPLETED
PropertyJob.result = PropertyAnalysisResult
```

---

## Key Configuration

| Setting | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama instance |
| `OLLAMA_MODEL` | `minicpm-v` | Vision model |
| `OLLAMA_TIMEOUT` | `120` | Seconds before vision times out |
| `MAX_CONCURRENT_JOBS` | `5` | Max parallel browser + vision instances |
| `satellite_zoom` | `19` | ESRI tile zoom level (19 = ~0.3m/px in USA) |
| `satellite_image_width` | `800` | Final satellite image size (px) |
| `playwright_headless` | `true` | Set false to watch captures live |

---

## Environment Setup

```
# Required for production
GOOGLE_MAPS_API_KEY=<your key>      # Street View access
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=minicpm-v

# Optional
ARCGIS_API_KEY=<your key>           # GIS parcel data
MAPBOX_TOKEN=<your token>           # Frontend map display
```

**Ollama setup:**
```bash
# Install Ollama: https://ollama.com
ollama pull minicpm-v
ollama serve   # starts on port 11434
```

**Backend:**
```bash
cd backend
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

---

## Accuracy & Tuning

**Vision model accuracy levers:**
- `temperature=0.1` — near-deterministic output (reduce to 0.0 for full determinism, but risks repetition loops)
- Satellite image at 768px max, street views at 640px — balanced quality vs. token budget
- Prompt uses "ONLY if" language to prevent the model over-triggering flags on normal properties

**Known model weaknesses:**
- `vacancy_signs`: minicpm-v tends to mark any property with no cars visible as vacant — this field is captured but intentionally NOT used as a standalone rejection or review trigger
- Rural properties with no Street View: handled by brightness check + fallback search (see Stage 3)
- Night-time or heavily overcast satellite images: confidence auto-drops below 0.65, forcing REVIEW

**Acceptance/rejection rate targets (Alterna SOP baseline):**
- APPROVED: ~60–70% of residential properties in target markets
- REJECTED: ~15–25% (fire damage, plywood, debris, banned facilities)
- NEEDS_HUMAN_REVIEW: ~10–20% (low confidence, mobile homes, industrial, ambiguous type)

---

## File Structure

```
Alterna-Project/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app, static file mounts
│   │   ├── config.py                  # pydantic-settings config
│   │   ├── models/
│   │   │   └── property.py            # All Pydantic models + SOP enums
│   │   ├── pipeline/
│   │   │   └── orchestrator.py        # Job lifecycle, asyncio.gather coordination
│   │   ├── services/
│   │   │   ├── gis_service.py         # GIS / parcel data lookup
│   │   │   ├── satellite_service.py   # ESRI tile stitching + OSM polygon overlay
│   │   │   ├── street_capture_service.py  # Playwright Street View capture
│   │   │   ├── vision_service.py      # Ollama / MiniCPM-V multimodal analysis
│   │   │   ├── rule_engine.py         # Deterministic SOP decision tree
│   │   │   └── report_generator.py   # JSON + PDF report output
│   │   ├── routers/
│   │   │   ├── properties.py          # POST /batch, GET /jobs/{id}
│   │   │   └── health.py              # GET /health
│   │   └── utils/
│   │       └── property_marker.py    # CV2 target marker overlay on street views
│   ├── data/
│   │   ├── images/
│   │   │   ├── satellite/             # {job_id}_satellite.jpg
│   │   │   └── street/                # {job_id}_sv_{left|center|right}.jpg
│   │   └── reports/                   # {job_id}_report.json
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── BatchPage.tsx          # CSV upload + batch progress UI
│       │   └── ResultsPage.tsx        # Per-property decision cards
│       ├── store/                     # Redux Toolkit slices
│       ├── services/                  # API client (axios)
│       └── components/                # Shared UI components
├── docker-compose.yml
└── .env.example
```

---

## Decisions We Deliberately Avoided

| Option | Why we rejected it |
|---|---|
| Database (Postgres/SQLite) | Jobs live in-memory; no persistence needed between runs; adds ops overhead |
| Message queue (Celery/Redis) | asyncio.Semaphore + asyncio.create_task is sufficient for 5-concurrent model; adds infra |
| WebSocket for live updates | Client polls every 2s — good enough; WebSockets add reconnect complexity |
| Cloud vision APIs | Per-call cost is prohibitive for bulk (100s of properties); data privacy |
| Scraping Street View pixels without Google Maps | No alternative source has comparable USA coverage |
| OCR on property signs | Too unreliable; vision model directly interprets the full scene |
| Database for image caching | JPEG files on disk indexed by job_id are simpler and fast enough |
