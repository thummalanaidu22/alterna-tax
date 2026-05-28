from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
 
 
# ---------------------------------------------------------------------------
# Core decision + property type
# ---------------------------------------------------------------------------
 
class Decision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
 
 
class PropertyType(str, Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    VACANT_LAND = "vacant_land"
    AGRICULTURE = "agriculture"
    MOBILE_HOME = "mobile_home"       # SOP: 2026 Mobile filter — separate type
    INDUSTRIAL = "industrial"         # SOP: 2026 Industrial filter
    UNKNOWN = "unknown"
 
 
# ---------------------------------------------------------------------------
# Parcel / lot geometry
# ---------------------------------------------------------------------------
 
class ParcelShape(str, Enum):
    RECTANGULAR = "rectangular"       # OK for agriculture
    SQUARE = "square"                 # OK for agriculture
    NARROW = "narrow"                 # REJECT vacant
    TRIANGLE = "triangle"             # REJECT vacant (not buildable)
    IRREGULAR = "irregular"           # flag for review
    UNKNOWN = "unknown"
 
 
class NeighborhoodDensity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"
 
 
# ---------------------------------------------------------------------------
# Water body  — SOP: pond / water-hole on land = REJECT agri;
#               lake / canal / ocean / bay = OK;  pool = OK
# ---------------------------------------------------------------------------
 
class WaterBodyType(str, Enum):
    NONE = "none"
    WATER_HOLE = "water_hole"   # saturated depression INSIDE parcel boundary
                                # SOP: "likely a leak" — REJECT agriculture
    POND = "pond"               # natural pond bordering parcel — flag for review
    POOL = "pool"               # swimming pool — OK
    LAKE = "lake"               # large lake — OK
    CANAL = "canal"             # canal — OK
    OCEAN = "ocean"             # ocean / bay — OK
    BAY = "bay"                 # bay — OK
    UNKNOWN = "unknown"
 
 
# ---------------------------------------------------------------------------
# Commercial sub-types  (SOP rejects hospitals, schools, religious, gas, auto)
# ---------------------------------------------------------------------------
 
class MedicalType(str, Enum):
    NONE = "none"
    HOSPITAL = "hospital"               # large hospital — REJECT
    CLINIC = "clinic"                   # doctor's office / clinic — OK
    DOCTORS_OFFICE = "doctors_office"   # doctor's office — OK
 
 
class SchoolType(str, Enum):
    NONE = "none"
    SCHOOL = "school"           # K-12 school — REJECT
    PRESCHOOL = "preschool"     # pre-school / daycare — OK
 
 
# NEW — SOP explicitly lists church / synagogue / mosque as reject conditions
class ReligiousBuildingType(str, Enum):
    NONE = "none"
    CHURCH = "church"           # REJECT
    SYNAGOGUE = "synagogue"     # REJECT
    MOSQUE = "mosque"           # REJECT
    TEMPLE = "temple"           # REJECT — covers all places of worship
    UNKNOWN = "unknown"
 
 
# NEW — gas station and auto repair moved out of free-text into typed enum
class CommercialRejectType(str, Enum):
    NONE = "none"
    GAS_STATION = "gas_station"     # REJECT
    AUTO_REPAIR = "auto_repair"     # REJECT
    UNKNOWN = "unknown"
 
 
# ---------------------------------------------------------------------------
# Core observation model — filled by vision AI + rule engine
# ---------------------------------------------------------------------------
 
class PropertyObservations(BaseModel):
 
    # --- Residential structural flags ---
    boarded_windows: bool = False           # plywood boards — REJECT
                                            # NOTE: hurricane shutters are NOT boarded
    hurricane_shutters: bool = False        # accordion / panel shutters — OK
    roof_damage: bool = False               # visible roof damage — REJECT
    visible_structure_damage: bool = False  # cracks, collapse, fire damage — REJECT
    structure_burned: bool = False          # burned-out structure — REJECT
    abandoned_appearance: bool = False      # boarded, no activity, overgrown — REJECT
    trash_or_debris: bool = False           # "complete mess" per SOP — REJECT
    vacancy_signs: bool = False             # overgrown lawn, no vehicles, neglected
    mobile_home: bool = False               # mobile / manufactured housing
    under_construction: bool = False        # structure mid-construction
 
    # --- Access / land ---
    road_access: bool = True                # parcel has any road access
    landlocked: bool = False                # no access roads at all — REJECT vacant + agri
 
    # NEW — SOP: "lots not facing a street" = reject for vacant
    street_frontage: bool = True            # parcel has direct frontage on a public street
    side_lot: bool = False                  # sliver / side lot with no usable frontage — REJECT vacant
 
    wooded: bool = False                    # heavily wooded — REJECT vacant
 
    # NEW — SOP: "look on aerial to see size vs other homes"
    lot_size_adequate_vs_neighborhood: bool = True
    # True = lot appears large enough relative to surrounding parcels to build on
    # Vision model must compare highlighted parcel to neighboring lots in the aerial
 
    # --- Water ---
    water_body_type: WaterBodyType = WaterBodyType.NONE
 
    # --- Shape & buildability ---
    parcel_shape: ParcelShape = ParcelShape.UNKNOWN
    buildable: bool = True
    has_structure: bool = False             # parcel has a house / building on it
 
    # NEW — SOP: "be careful — street view is a building, GSI aerial is only parking lot"
    parcel_is_parking_only: bool = False
    # True = aerial shows parcel boundary contains only a parking lot, no structure
 
    # --- Commercial classification ---
    # Replaced free-text commercial_type_detected with typed enums:
    medical_type: MedicalType = MedicalType.NONE
    school_type: SchoolType = SchoolType.NONE
    religious_building_type: ReligiousBuildingType = ReligiousBuildingType.NONE  # NEW
    commercial_reject_type: CommercialRejectType = CommercialRejectType.NONE     # NEW
 
    # --- Agriculture-specific positive criteria ---
    # SOP: approve agri ONLY if all three are true AND no water_hole
    agri_has_house_on_parcel: bool = False      # must be True for agri approve
    agri_fronts_road: bool = False              # must be True for agri approve
    agri_parcel_shape_regular: bool = False     # square/rect — must be True for agri approve
 
    # --- Context ---
    neighborhood_density: NeighborhoodDensity = NeighborhoodDensity.UNKNOWN
 
 
# ---------------------------------------------------------------------------
# Analysis result
# ---------------------------------------------------------------------------
 
class PropertyAnalysisResult(BaseModel):
    decision: Decision
    property_type: PropertyType
    confidence_score: float = Field(ge=0.0, le=1.0)
    observations: PropertyObservations
    rejection_reasons: List[str] = []
    human_review_reasons: List[str] = []
    summary: str
 
    # NEW — SOP: aerial is the determining factor; audit trail for which image drove decision
    primary_image_used: str = "aerial"      # "aerial" | "street_view" | "both"
    aerial_image_available: bool = True     # if False → force NEEDS_HUMAN_REVIEW
 
 
# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
 
class PropertyRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    property_id: Optional[str] = None
    property_type_hint: Optional[str] = None    # from CSV zoning data (Resi/Comm/Agri/etc.)
 
 
class BatchPropertyRequest(BaseModel):
    properties: List[PropertyRequest]
    batch_id: Optional[str] = None
 
 
# ---------------------------------------------------------------------------
# Pipeline / job tracking
# ---------------------------------------------------------------------------
 
class PipelineStageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
 
 
class PipelineStage(BaseModel):
    name: str
    status: PipelineStageStatus = PipelineStageStatus.PENDING
    error: Optional[str] = None
    duration_ms: Optional[float] = None
 
 
class PropertyJobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
 
 
class PropertyJob(BaseModel):
    job_id: str
    property_id: Optional[str] = None
    latitude: float
    longitude: float
    status: PropertyJobStatus = PropertyJobStatus.QUEUED
    stages: List[PipelineStage] = []
    result: Optional[PropertyAnalysisResult] = None
    created_at: str
    updated_at: str
    error: Optional[str] = None
 
    # Zoning/type hint from CSV — passed to vision model to improve classification accuracy
    property_type_hint: Optional[str] = None

    # Image capture metadata — used by frontend to avoid requesting missing files
    street_view_count: int = 0             # number of street view images actually saved (0–3)

    # NEW — ground truth for accuracy measurement
    # Reviewers set this after manual inspection in Alterna (tag = OK-2026 or Kill-2026)
    human_verdict: Optional[str] = None    # "OK_2026" | "KILL_2026" | None (unreviewed)
    reviewer_notes: Optional[str] = None
 
 
class BatchJobStatus(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    queued: int
    processing: int
    jobs: List[PropertyJob] = []
 
 
# ---------------------------------------------------------------------------
# SOP Rule Engine reference
# ---------------------------------------------------------------------------
#
# RESIDENTIAL — reject if ANY of:
#   boarded_windows=True (plywood only; hurricane_shutters=True is OK)
#   trash_or_debris=True
#   structure_burned=True
#   roof_damage=True
#   abandoned_appearance=True
#   visible_structure_damage=True
#
# COMMERCIAL — reject if ANY of:
#   medical_type = HOSPITAL
#   school_type = SCHOOL
#   religious_building_type != NONE
#   commercial_reject_type != NONE  (gas station, auto repair)
#   parcel_is_parking_only = True
#   OK: medical_type in (CLINIC, DOCTORS_OFFICE)
#   OK: school_type = PRESCHOOL
#   OK: retail, condos, apartments
#
# VACANT (resi or comm) — reject if ANY of:
#   parcel_shape in (NARROW, TRIANGLE)
#   side_lot = True
#   street_frontage = False
#   wooded = True
#   landlocked = True
#   lot_size_adequate_vs_neighborhood = False
#
# AGRICULTURE — reject if ANY of:
#   water_body_type = WATER_HOLE
#   landlocked = True
#   parcel_shape in (TRIANGLE, NARROW)
#   Approve ONLY if ALL: agri_has_house_on_parcel AND agri_fronts_road
#                        AND agri_parcel_shape_regular AND water_body_type != WATER_HOLE
#
# ALL TYPES — force NEEDS_HUMAN_REVIEW if:
#   aerial_image_available = False
#   confidence_score < 0.65
 






