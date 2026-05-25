export type Decision = "APPROVED" | "REJECTED" | "NEEDS_HUMAN_REVIEW";
export type PropertyType =
  | "residential"
  | "commercial"
  | "vacant_land"
  | "agriculture"
  | "mobile_home"
  | "industrial"
  | "unknown";
export type ParcelShape = "rectangular" | "square" | "narrow" | "triangle" | "irregular" | "unknown";
export type NeighborhoodDensity = "low" | "medium" | "high" | "unknown";
export type JobStatus = "queued" | "processing" | "completed" | "failed";
export type StageStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface PropertyObservations {
  boarded_windows: boolean;
  roof_damage: boolean;
  visible_structure_damage: boolean;
  abandoned_appearance: boolean;
  trash_or_debris: boolean;
  vacancy_signs: boolean;
  road_access: boolean;
  landlocked: boolean;
  wooded: boolean;
  has_structure: boolean;
  parcel_shape: ParcelShape;
  buildable: boolean;
  neighborhood_density: NeighborhoodDensity;
}

export interface AnalysisResult {
  decision: Decision;
  property_type: PropertyType;
  confidence_score: number;
  observations: PropertyObservations;
  rejection_reasons: string[];
  human_review_reasons: string[];
  summary: string;
  aerial_image_available: boolean;
  primary_image_used: string;
}

export interface PipelineStage {
  name: string;
  status: StageStatus;
  error?: string;
  duration_ms?: number;
}

export interface PropertyJob {
  job_id: string;
  property_id?: string;
  latitude: number;
  longitude: number;
  status: JobStatus;
  stages: PipelineStage[];
  result?: AnalysisResult;
  created_at: string;
  updated_at: string;
  error?: string;
  human_verdict?: string;
  reviewer_notes?: string;
}

export interface BatchJobStatus {
  batch_id: string;
  total: number;
  completed: number;
  failed: number;
  queued: number;
  processing: number;
  jobs: PropertyJob[];
}

export interface PropertyRequest {
  latitude: number;
  longitude: number;
  property_id?: string;
}

export interface BatchRequest {
  properties: PropertyRequest[];
  batch_id?: string;
}

export interface ReviewRequest {
  verdict: "approved" | "rejected";
  notes?: string;
}
