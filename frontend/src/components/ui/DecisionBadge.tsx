import type { Decision } from "../../types/property";

const CONFIG: Record<Decision, { label: string; className: string }> = {
  APPROVED: { label: "Approved", className: "bg-emerald-500/20 text-emerald-400 ring-1 ring-emerald-500/40" },
  REJECTED: { label: "Rejected", className: "bg-red-500/20 text-red-400 ring-1 ring-red-500/40" },
  NEEDS_HUMAN_REVIEW: { label: "Review", className: "bg-amber-500/20 text-amber-400 ring-1 ring-amber-500/40" },
};

export function DecisionBadge({ decision }: { decision: Decision }) {
  const { label, className } = CONFIG[decision];
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold uppercase tracking-wide ${className}`}>
      {label}
    </span>
  );
}
