import { useQuery } from "@tanstack/react-query";
import { CheckCircle, XCircle, AlertTriangle, Activity, Cpu } from "lucide-react";
import { propertyApi } from "../services/api";
import { Card } from "../components/ui/Card";
import { StatusBadge } from "../components/ui/StatusBadge";
import { DecisionBadge } from "../components/ui/DecisionBadge";
import { Spinner } from "../components/ui/Spinner";
import { formatDistanceToNow } from "date-fns";

export function DashboardPage() {
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: propertyApi.getHealth,
    refetchInterval: 15000,
  });

  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => propertyApi.listJobs(100),
    refetchInterval: 10000,
  });

  const stats = {
    total: jobs.length,
    approved: jobs.filter((j) => j.result?.decision === "APPROVED").length,
    rejected: jobs.filter((j) => j.result?.decision === "REJECTED").length,
    review: jobs.filter((j) => j.result?.decision === "NEEDS_HUMAN_REVIEW").length,
    processing: jobs.filter((j) => j.status === "queued" || j.status === "processing").length,
  };

  const recentJobs = jobs.slice(0, 8);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-gray-500 text-sm mt-1">Property Intelligence System overview</p>
        </div>
        {health && (
          <div className="flex items-center gap-2 text-xs">
            <span className={`w-2 h-2 rounded-full ${health.ollama === "connected" ? "bg-emerald-400" : "bg-red-400"}`} />
            <span className="text-gray-500">Ollama: <span className={health.ollama === "connected" ? "text-emerald-400" : "text-red-400"}>{health.ollama}</span></span>
            <span className="text-gray-700">|</span>
            <span className="text-gray-500">Model: <span className="text-gray-300">{health.model}</span></span>
          </div>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {[
          { label: "Total Jobs", val: stats.total, icon: <Activity className="w-4 h-4" />, color: "text-white" },
          { label: "Approved", val: stats.approved, icon: <CheckCircle className="w-4 h-4" />, color: "text-emerald-400" },
          { label: "Rejected", val: stats.rejected, icon: <XCircle className="w-4 h-4" />, color: "text-red-400" },
          { label: "Review", val: stats.review, icon: <AlertTriangle className="w-4 h-4" />, color: "text-amber-400" },
          { label: "Active", val: stats.processing, icon: <Cpu className="w-4 h-4" />, color: "text-blue-400" },
        ].map((s) => (
          <Card key={s.label} className="text-center">
            <div className={`flex justify-center mb-2 ${s.color} opacity-70`}>{s.icon}</div>
            <div className={`text-3xl font-bold ${s.color}`}>{s.val}</div>
            <div className="text-xs text-gray-600 mt-1">{s.label}</div>
          </Card>
        ))}
      </div>

      {/* Approval rate bar */}
      {stats.total > 0 && (
        <Card>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-gray-300">Decision Distribution</h3>
            <span className="text-xs text-gray-500">{stats.total} analyzed</span>
          </div>
          <div className="flex h-2.5 rounded-full overflow-hidden gap-0.5">
            {stats.approved > 0 && (
              <div className="bg-emerald-500 transition-all" style={{ width: `${(stats.approved / stats.total) * 100}%` }} />
            )}
            {stats.review > 0 && (
              <div className="bg-amber-500 transition-all" style={{ width: `${(stats.review / stats.total) * 100}%` }} />
            )}
            {stats.rejected > 0 && (
              <div className="bg-red-500 transition-all" style={{ width: `${(stats.rejected / stats.total) * 100}%` }} />
            )}
          </div>
          <div className="flex gap-4 mt-2">
            {[
              { label: "Approved", pct: stats.total ? Math.round((stats.approved / stats.total) * 100) : 0, color: "text-emerald-400" },
              { label: "Review", pct: stats.total ? Math.round((stats.review / stats.total) * 100) : 0, color: "text-amber-400" },
              { label: "Rejected", pct: stats.total ? Math.round((stats.rejected / stats.total) * 100) : 0, color: "text-red-400" },
            ].map((s) => (
              <span key={s.label} className={`text-xs ${s.color}`}>{s.label}: {s.pct}%</span>
            ))}
          </div>
        </Card>
      )}

      {/* Recent jobs */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-300 mb-4">Recent Jobs</h3>
        {isLoading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : recentJobs.length === 0 ? (
          <p className="text-sm text-gray-600 text-center py-8">No jobs yet. Run your first analysis.</p>
        ) : (
          <div className="space-y-2">
            {recentJobs.map((job) => (
              <div key={job.job_id} className="flex items-center gap-3 py-2 px-3 rounded-lg hover:bg-gray-800/50 transition-colors">
                <div className="w-2 h-2 rounded-full bg-gray-700 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400 font-mono">
                      {job.latitude.toFixed(4)}, {job.longitude.toFixed(4)}
                    </span>
                    {job.property_id && (
                      <span className="text-xs text-gray-600">{job.property_id}</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <StatusBadge status={job.status} />
                  {job.result && <DecisionBadge decision={job.result.decision} />}
                  <span className="text-xs text-gray-700">
                    {formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
