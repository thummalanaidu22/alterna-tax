import { CheckCircle, XCircle, Clock, Loader } from "lucide-react";
import type { PipelineStage } from "../../types/property";

const STAGE_LABELS: Record<string, string> = {
  gis_fetch: "GIS Parcel Fetch",
  satellite_capture: "Satellite Image",
  street_capture: "Street View Capture",
  vision_analysis: "AI Vision Analysis",
  rule_engine: "SOP Rule Engine",
  report_generation: "Report Generation",
};

const STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <Clock className="w-4 h-4 text-gray-600" />,
  running: <Loader className="w-4 h-4 text-blue-400 animate-spin" />,
  completed: <CheckCircle className="w-4 h-4 text-emerald-400" />,
  failed: <XCircle className="w-4 h-4 text-red-400" />,
  skipped: <Clock className="w-4 h-4 text-gray-700" />,
};

export function PipelineProgress({ stages }: { stages: PipelineStage[] }) {
  return (
    <div className="space-y-2">
      {stages.map((stage, i) => (
        <div key={i} className="flex items-center gap-3 py-2 px-3 rounded-lg bg-gray-800/60">
          {STATUS_ICON[stage.status] ?? STATUS_ICON.pending}
          <span className="flex-1 text-sm text-gray-300">{STAGE_LABELS[stage.name] ?? stage.name}</span>
          {stage.duration_ms != null && (
            <span className="text-xs text-gray-600">{stage.duration_ms.toFixed(0)}ms</span>
          )}
          {stage.error && (
            <span className="text-xs text-red-400 truncate max-w-xs">{stage.error}</span>
          )}
        </div>
      ))}
    </div>
  );
}
