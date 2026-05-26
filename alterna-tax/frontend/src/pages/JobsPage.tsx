import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { propertyApi } from "../services/api";
import { Card } from "../components/ui/Card";
import { StatusBadge } from "../components/ui/StatusBadge";
import { DecisionBadge } from "../components/ui/DecisionBadge";
import { AnalysisResultCard } from "../components/property/AnalysisResultCard";
import { PipelineProgress } from "../components/property/PipelineProgress";
import { ImageGallery } from "../components/property/ImageGallery";
import { formatDistanceToNow } from "date-fns";
import { useAppSelector, useAppDispatch } from "../store";
import { upsertJob } from "../store/jobsSlice";

export function JobsPage() {
  const dispatch = useAppDispatch();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Read from Redux — empty on page refresh, populates as jobs are submitted this session
  const jobs = useAppSelector((s) => s.jobs.recentJobs);

  const { data: selectedJob } = useQuery({
    queryKey: ["job", selectedId],
    queryFn: () => propertyApi.getJob(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "queued" || s === "processing" ? 2000 : false;
    },
  });

  // Keep Redux in sync with latest polled state for the selected job
  useEffect(() => {
    if (selectedJob) dispatch(upsertJob(selectedJob));
  }, [selectedJob, dispatch]);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">All Jobs</h1>
        <p className="text-gray-500 text-sm mt-1">{jobs.length} total jobs this session</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-5">
        {/* Jobs list */}
        <div className="space-y-2">
          {jobs.length === 0 && (
            <Card>
              <p className="text-sm text-gray-500 text-center py-8">No jobs yet. Run an analysis first.</p>
            </Card>
          )}
          {jobs.map((job) => (
            <div
              key={job.job_id}
              onClick={() => setSelectedId(job.job_id)}
              className={`cursor-pointer rounded-xl p-4 border transition-all ${
                selectedId === job.job_id
                  ? "bg-gray-800 border-blue-600"
                  : "bg-gray-900 border-gray-800 hover:border-gray-700"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    {job.property_id && (
                      <span className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">{job.property_id}</span>
                    )}
                    <StatusBadge status={job.status} />
                  </div>
                  <p className="text-xs text-gray-500 font-mono">
                    {job.latitude.toFixed(5)}, {job.longitude.toFixed(5)}
                  </p>
                  <p className="text-xs text-gray-700 mt-1">
                    {formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {job.result && <DecisionBadge decision={job.result.decision} />}
                  <ChevronRight className="w-4 h-4 text-gray-700" />
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Detail panel */}
        <div>
          {!selectedId && (
            <Card className="text-center py-20">
              <p className="text-gray-600 text-sm">Select a job to view details</p>
            </Card>
          )}
          {selectedJob && (
            <div className="space-y-4">
              <Card>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs text-gray-600 font-mono">{selectedJob.job_id}</p>
                    {selectedJob.property_id && (
                      <p className="text-sm font-medium text-white mt-0.5">{selectedJob.property_id}</p>
                    )}
                  </div>
                  <StatusBadge status={selectedJob.status} />
                </div>
              </Card>

              {selectedJob.stages.length > 0 && (
                <Card>
                  <h3 className="text-sm font-semibold text-gray-300 mb-3">Pipeline</h3>
                  <PipelineProgress stages={selectedJob.stages} />
                </Card>
              )}

              {selectedJob.status === "completed" && (
                <Card>
                  <h3 className="text-sm font-semibold text-gray-300 mb-3">Images</h3>
                  <ImageGallery jobId={selectedJob.job_id} streetViewCount={selectedJob.street_view_count} />
                </Card>
              )}

              {selectedJob.result && <AnalysisResultCard result={selectedJob.result} />}

              {selectedJob.status === "failed" && selectedJob.error && (
                <Card className="border-red-900/50 bg-red-950/20">
                  <p className="text-sm text-red-400">Pipeline failed: {selectedJob.error}</p>
                </Card>
              )}

              {selectedJob.status === "completed" && (
                <div className="flex gap-3 px-1">
                  <a href={`/reports/${selectedJob.job_id}_report.html`} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-blue-400 hover:text-blue-300 underline underline-offset-2">
                    HTML Report
                  </a>
                  <a href={`/reports/${selectedJob.job_id}_report.json`} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-blue-400 hover:text-blue-300 underline underline-offset-2">
                    JSON Report
                  </a>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
