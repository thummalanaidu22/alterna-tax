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


class PropertyObservations(BaseModel):
    boarded_windows: bool = False
    roof_damage: bool = False
    visible_structure_damage: bool = False
    abandoned_appearance: bool = False
    trash_or_debris: bool = False
    road_access: bool = True
    landlocked: bool = False
    wooded: bool = False
    water_body_present: bool = False
    parcel_shape: ParcelShape = ParcelShape.UNKNOWN
    buildable: bool = True
    commercial_type_detected: str = "none"
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
