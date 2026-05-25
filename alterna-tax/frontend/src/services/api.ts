import axios from "axios";
import type {
  PropertyJob,
  BatchJobStatus,
  PropertyRequest,
  BatchRequest,
  ReviewRequest,
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

  listJobs: (limit = 100) =>
    http.get<PropertyJob[]>(`/properties/jobs?limit=${limit}`).then((r) => r.data),

  getBatchStatus: (batchId: string) =>
    http.get<BatchJobStatus>(`/properties/batch/${batchId}`).then((r) => r.data),

  getHealth: () =>
    http
      .get<{ status: string; ollama: string; model: string; version: string }>("/health")
      .then((r) => r.data),

  getReviewQueue: (limit = 200) =>
    http.get<PropertyJob[]>(`/properties/review-queue?limit=${limit}`).then((r) => r.data),

  reviewJob: (jobId: string, req: ReviewRequest) =>
    http
      .put<{ job_id: string; verdict: string; status: string }>(
        `/properties/jobs/${jobId}/review`,
        req
      )
      .then((r) => r.data),
};

/** Connect to the WebSocket for a specific job and receive live updates. */
export function createJobWebSocket(
  jobId: string,
  onUpdate: (job: PropertyJob) => void,
  onClose?: () => void
): WebSocket {
  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  const wsBase = `${wsProtocol}://${window.location.host}`;
  const ws = new WebSocket(`${wsBase}/api/ws/jobs/${jobId}`);

  ws.onmessage = (evt) => {
    try {
      const job: PropertyJob = JSON.parse(evt.data);
      onUpdate(job);
    } catch {
      /* ignore malformed frames */
    }
  };

  ws.onclose = () => onClose?.();
  ws.onerror = () => ws.close();

  return ws;
}
