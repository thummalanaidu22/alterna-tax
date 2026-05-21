from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class Decision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class PropertyType(str, Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    VACANT_LAND = "vacant_land"
    AGRICULTURE = "agriculture"
    
    UNKNOWN = "unknown"


class ParcelShape(str, Enum):
    RECTANGULAR = "rectangular"
    SQUARE = "square"
    NARROW = "narrow"
    TRIANGLE = "triangle"
    IRREGULAR = "irregular"
    UNKNOWN = "unknown"


class NeighborhoodDensity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class WaterBodyType(str, Enum):
    NONE = "none"
    POND = "pond"        # small isolated water hole on land — REJECT agriculture
    POOL = "pool"        # swimming pool — OK
    LAKE = "lake"        # large lake bordering parcel — OK
    CANAL = "canal"      # canal — OK
    OCEAN = "ocean"      # ocean/bay — OK
    BAY = "bay"          # bay — OK
    UNKNOWN = "unknown"


class MedicalType(str, Enum):
    NONE = "none"
    HOSPITAL = "hospital"         # large hospital — REJECT
    CLINIC = "clinic"             # doctor's office / clinic — OK
    DOCTORS_OFFICE = "doctors_office"  # doctor's office — OK


class SchoolType(str, Enum):
    NONE = "none"
    SCHOOL = "school"             # K-12 school — REJECT
    PRESCHOOL = "preschool"       # pre-school / daycare — OK


class PropertyObservations(BaseModel):
    # Residential flags
    boarded_windows: bool = False            # plywood boards — REJECT
    hurricane_shutters: bool = False         # accordion/panel shutters — OK
    roof_damage: bool = False
    visible_structure_damage: bool = False
    structure_burned: bool = False
    abandoned_appearance: bool = False
    trash_or_debris: bool = False
    vacancy_signs: bool = False              # overgrown, no vehicles, neglected
    mobile_home: bool = False               # mobile/manufactured housing
    under_construction: bool = False        # structure mid-construction

    # Access / land
    road_access: bool = True
    landlocked: bool = False
    wooded: bool = False

    # Water
    water_body_type: WaterBodyType = WaterBodyType.NONE

    # Shape & buildability
    parcel_shape: ParcelShape = ParcelShape.UNKNOWN
    buildable: bool = True
    has_structure: bool = False              # parcel has a house/building on it

    # Commercial classification
    commercial_type_detected: str = "none"
    medical_type: MedicalType = MedicalType.NONE
    school_type: SchoolType = SchoolType.NONE

    neighborhood_density: NeighborhoodDensity = NeighborhoodDensity.UNKNOWN


class PropertyAnalysisResult(BaseModel):
    decision: Decision
    property_type: PropertyType
    confidence_score: float = Field(ge=0.0, le=1.0)
    observations: PropertyObservations
    rejection_reasons: List[str] = []
    human_review_reasons: List[str] = []
    summary: str


class PropertyRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    property_id: Optional[str] = None
    property_type_hint: Optional[str] = None  # from CSV zoning data


class BatchPropertyRequest(BaseModel):
    properties: List[PropertyRequest]
    batch_id: Optional[str] = None


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


class BatchJobStatus(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    queued: int
    processing: int
    jobs: List[PropertyJob] = []
