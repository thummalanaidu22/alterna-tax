import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { propertyApi } from "../services/api";
import type { PropertyJob } from "../types/property";

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;

const DECISION_COLOR: Record<string, string> = {
  APPROVED: "#22c55e",
  REJECTED: "#ef4444",
  NEEDS_HUMAN_REVIEW: "#f59e0b",
};

function confidenceLabel(c: number) {
  return `${Math.round(c * 100)}%`;
}

function NoTokenBanner() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
      <div className="w-16 h-16 rounded-2xl bg-amber-500/20 flex items-center justify-center">
        <span className="text-3xl">🗺️</span>
      </div>
      <div>
        <h2 className="text-lg font-semibold text-white mb-1">Mapbox token required</h2>
        <p className="text-sm text-gray-400 max-w-sm">
          Add your free Mapbox token to{" "}
          <code className="text-amber-400 bg-gray-800 px-1 rounded">frontend/.env.local</code>:
        </p>
        <pre className="mt-3 text-xs bg-gray-800 text-green-400 rounded-lg px-4 py-3 text-left inline-block">
          VITE_MAPBOX_TOKEN=pk.eyJ1Ij...
        </pre>
        <p className="mt-3 text-xs text-gray-500">
          Get a free token at{" "}
          <span className="text-blue-400">mapbox.com → Account → Tokens</span>
        </p>
      </div>
    </div>
  );
}

export function MapPage() {
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<PropertyJob | null>(null);

  const { data: jobs = [] } = useQuery({
    queryKey: ["jobs-map"],
    queryFn: () => propertyApi.listJobs(500),
    refetchInterval: 30_000,
  });

  const completedJobs = jobs.filter((j) => j.status === "completed" && j.result);

  // Initialise map
  useEffect(() => {
    if (!MAPBOX_TOKEN || MAPBOX_TOKEN === "your_mapbox_token") return;
    if (!containerRef.current || mapRef.current) return;

    mapboxgl.accessToken = MAPBOX_TOKEN;
    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: "mapbox://styles/mapbox/dark-v11",
      center: [-98.5, 39.5],
      zoom: 4,
    });
    map.addControl(new mapboxgl.NavigationControl(), "top-right");
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Plot markers whenever jobs change
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Remove existing markers (stored on DOM)
    document.querySelectorAll(".prop-marker").forEach((el) => el.remove());

    completedJobs.forEach((job) => {
      if (!job.result) return;
      const color = DECISION_COLOR[job.result.decision] ?? "#6b7280";

      const el = document.createElement("div");
      el.className = "prop-marker";
      el.style.cssText = `
        width: 14px; height: 14px;
        border-radius: 50%;
        background: ${color};
        border: 2px solid rgba(255,255,255,0.6);
        cursor: pointer;
        transition: transform 0.15s;
      `;
      el.addEventListener("mouseenter", () => (el.style.transform = "scale(1.5)"));
      el.addEventListener("mouseleave", () => (el.style.transform = "scale(1)"));
      el.addEventListener("click", () => setSelected(job));

      new mapboxgl.Marker({ element: el })
        .setLngLat([job.longitude, job.latitude])
        .addTo(map);
    });
  }, [completedJobs]);

  const isNoToken = !MAPBOX_TOKEN || MAPBOX_TOKEN === "your_mapbox_token";

  const counts = {
    approved: completedJobs.filter((j) => j.result?.decision === "APPROVED").length,
    rejected: completedJobs.filter((j) => j.result?.decision === "REJECTED").length,
    review: completedJobs.filter((j) => j.result?.decision === "NEEDS_HUMAN_REVIEW").length,
  };

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Property Map</h1>
          <p className="text-sm text-gray-500">{completedJobs.length} analysed properties</p>
        </div>
        <div className="flex gap-3 text-sm">
          {[
            { label: "Approved", color: "bg-green-500", count: counts.approved },
            { label: "Rejected", color: "bg-red-500", count: counts.rejected },
            { label: "Review", color: "bg-amber-500", count: counts.review },
          ].map(({ label, color, count }) => (
            <div key={label} className="flex items-center gap-1.5 text-gray-300">
              <span className={`w-2.5 h-2.5 rounded-full ${color}`} />
              {label}: <span className="font-semibold text-white">{count}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="flex gap-4 flex-1 min-h-0">
        {/* Map container */}
        <div className="relative flex-1 rounded-xl overflow-hidden bg-gray-900 border border-gray-800">
          {isNoToken ? (
            <NoTokenBanner />
          ) : (
            <div ref={containerRef} className="absolute inset-0" />
          )}
        </div>

        {/* Selected job detail */}
        {selected && selected.result && (
          <div className="w-72 shrink-0 bg-gray-900 border border-gray-800 rounded-xl p-4 flex flex-col gap-3 overflow-y-auto">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-gray-500">
                  {selected.property_id ?? selected.job_id.slice(0, 8)}
                </p>
                <p className="text-xs text-gray-600">
                  {selected.latitude.toFixed(5)}, {selected.longitude.toFixed(5)}
                </p>
              </div>
              <button
                onClick={() => setSelected(null)}
                className="text-gray-600 hover:text-gray-300 text-lg leading-none"
              >
                ×
              </button>
            </div>

            <div
              className="text-sm font-semibold px-2 py-1 rounded text-center"
              style={{
                background: (DECISION_COLOR[selected.result.decision] ?? "#6b7280") + "25",
                color: DECISION_COLOR[selected.result.decision] ?? "#6b7280",
              }}
            >
              {selected.result.decision.replace(/_/g, " ")}
            </div>

            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="bg-gray-800 rounded p-2">
                <p className="text-gray-500">Type</p>
                <p className="text-white capitalize">
                  {selected.result.property_type.replace(/_/g, " ")}
                </p>
              </div>
              <div className="bg-gray-800 rounded p-2">
                <p className="text-gray-500">Confidence</p>
                <p className="text-white">{confidenceLabel(selected.result.confidence_score)}</p>
              </div>
            </div>

            {selected.result.rejection_reasons.length > 0 && (
              <div>
                <p className="text-xs text-gray-500 mb-1">Rejection reasons</p>
                <ul className="text-xs text-red-400 space-y-0.5">
                  {selected.result.rejection_reasons.map((r, i) => (
                    <li key={i}>• {r}</li>
                  ))}
                </ul>
              </div>
            )}

            {selected.result.human_review_reasons.length > 0 && (
              <div>
                <p className="text-xs text-gray-500 mb-1">Review flags</p>
                <ul className="text-xs text-amber-400 space-y-0.5">
                  {selected.result.human_review_reasons.map((r, i) => (
                    <li key={i}>• {r}</li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-xs text-gray-400 border-t border-gray-800 pt-2">
              {selected.result.summary}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
