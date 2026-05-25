import { CheckCircle, XCircle, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import type { AnalysisResult } from "../../types/property";
import { DecisionBadge } from "../ui/DecisionBadge";
import { Card } from "../ui/Card";

const DECISION_ICONS = {
  APPROVED: <CheckCircle className="w-8 h-8 text-emerald-400" />,
  REJECTED: <XCircle className="w-8 h-8 text-red-400" />,
  NEEDS_HUMAN_REVIEW: <AlertTriangle className="w-8 h-8 text-amber-400" />,
};

const OBS_LABELS: Record<string, string> = {
  boarded_windows: "Boarded Windows",
  roof_damage: "Roof Damage",
  visible_structure_damage: "Structure Damage",
  abandoned_appearance: "Abandoned",
  trash_or_debris: "Trash / Debris",
  road_access: "Road Access",
  landlocked: "Landlocked",
  wooded: "Heavily Wooded",
  water_body_present: "Water Body Present",
  buildable: "Buildable",
};

export function AnalysisResultCard({ result }: { result: AnalysisResult }) {
  const [obsOpen, setObsOpen] = useState(false);

  return (
    <div className="space-y-4">
      {/* Header */}
      <Card>
        <div className="flex items-start gap-4">
          {DECISION_ICONS[result.decision]}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-1">
              <DecisionBadge decision={result.decision} />
              <span className="text-xs text-gray-500 capitalize">{result.property_type.replace("_", " ")}</span>
              <span className="text-xs text-gray-500 ml-auto">
                Confidence: <span className="text-gray-300 font-medium">{Math.round(result.confidence_score * 100)}%</span>
              </span>
            </div>
            <p className="text-sm text-gray-400 leading-relaxed">{result.summary}</p>
          </div>
        </div>
      </Card>

      {/* Rejection / Review reasons */}
      {result.rejection_reasons.length > 0 && (
        <Card className="border-red-900/50 bg-red-950/20">
          <h4 className="text-sm font-semibold text-red-400 mb-2 flex items-center gap-2">
            <XCircle className="w-4 h-4" /> Rejection Reasons
          </h4>
          <ul className="space-y-1">
            {result.rejection_reasons.map((r, i) => (
              <li key={i} className="text-sm text-red-300/80 flex items-start gap-2">
                <span className="mt-1.5 w-1 h-1 rounded-full bg-red-500 shrink-0" />
                {r}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.human_review_reasons.length > 0 && (
        <Card className="border-amber-900/50 bg-amber-950/20">
          <h4 className="text-sm font-semibold text-amber-400 mb-2 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> Review Flags
          </h4>
          <ul className="space-y-1">
            {result.human_review_reasons.map((r, i) => (
              <li key={i} className="text-sm text-amber-300/80 flex items-start gap-2">
                <span className="mt-1.5 w-1 h-1 rounded-full bg-amber-500 shrink-0" />
                {r}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Observations toggle */}
      <Card>
        <button
          onClick={() => setObsOpen(!obsOpen)}
          className="flex items-center justify-between w-full text-sm font-semibold text-gray-300"
        >
          <span>Observations</span>
          {obsOpen ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
        </button>
        {obsOpen && (
          <div className="mt-4 grid grid-cols-2 gap-2">
            {Object.entries(OBS_LABELS).map(([key, label]) => {
              const val = result.observations[key as keyof typeof result.observations];
              const isFlag = typeof val === "boolean";
              const positive = val === true;
              const isRoadAccess = key === "road_access" || key === "buildable";
              const good = isRoadAccess ? positive : !positive;
              return (
                <div key={key} className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-gray-800/50">
                  <span className="text-xs text-gray-400">{label}</span>
                  {isFlag ? (
                    <span className={`text-xs font-medium ${good ? "text-emerald-400" : "text-red-400"}`}>
                      {positive ? "Yes" : "No"}
                    </span>
                  ) : (
                    <span className="text-xs font-medium text-gray-300">{String(val)}</span>
                  )}
                </div>
              );
            })}
            <div className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-gray-800/50">
              <span className="text-xs text-gray-400">Parcel Shape</span>
              <span className="text-xs font-medium text-gray-300 capitalize">{result.observations.parcel_shape}</span>
            </div>
            <div className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-gray-800/50">
              <span className="text-xs text-gray-400">Density</span>
              <span className="text-xs font-medium text-gray-300 capitalize">{result.observations.neighborhood_density}</span>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
