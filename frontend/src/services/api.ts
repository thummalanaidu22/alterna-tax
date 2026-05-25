import axios from "axios";
import type {
  PropertyJob,
  BatchJobStatus,
  PropertyRequest,
  BatchRequest,
} from "../types/property";

const _apiKey = import.meta.env.VITE_API_KEY as string | undefined;

const http = axios.create({
  baseURL: "/api",
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
    ...(_apiKey ? { "X-API-Key": _apiKey } : {}),
  },
});

export const propertyApi = {
  analyzeProperty: (req: PropertyRequest) =>
    http.post<PropertyJob>("/properties/analyze", req).then((r) => r.data),

  analyzeBatch: (req: BatchRequest) =>
    http
      .post<{ batch_id: string; total: number }>("/properties/batch", req)
      .then((r) => r.data),

  getJob: (jobId: string) =>
    http.get<PropertyJob>(`/properties/jobs/${jobId}`).then((r) => r.data),

  listJobs: (limit = 50) =>
    http.get<PropertyJob[]>(`/properties/jobs?limit=${limit}`).then((r) => r.data),

  getBatchStatus: (batchId: string) =>
    http.get<BatchJobStatus>(`/properties/batch/${batchId}`).then((r) => r.data),

  getHealth: () =>
    http.get<{ status: string; ollama: string; model: string; version: string }>("/health").then((r) => r.data),
};
