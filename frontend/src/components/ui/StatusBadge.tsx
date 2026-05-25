import type { JobStatus } from "../../types/property";

const CONFIG: Record<JobStatus, { label: string; className: string; dot: string }> = {
  queued: { label: "Queued", className: "text-gray-400", dot: "bg-gray-500" },
  processing: { label: "Processing", className: "text-blue-400", dot: "bg-blue-400 animate-pulse" },
  completed: { label: "Completed", className: "text-emerald-400", dot: "bg-emerald-400" },
  failed: { label: "Failed", className: "text-red-400", dot: "bg-red-400" },
};

export function StatusBadge({ status }: { status: JobStatus }) {
  const { label, className, dot } = CONFIG[status];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${className}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {label}
    </span>
  );
}
