import { useState, useEffect } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { MapPin, Search } from "lucide-react";
import { propertyApi } from "../services/api";
import { useAppDispatch } from "../store";
import { upsertJob } from "../store/jobsSlice";
import { Card } from "../components/ui/Card";
import { Spinner } from "../components/ui/Spinner";
import { StatusBadge } from "../components/ui/StatusBadge";
import { AnalysisResultCard } from "../components/property/AnalysisResultCard";
import { PipelineProgress } from "../components/property/PipelineProgress";
import { ImageGallery } from "../components/property/ImageGallery";

export function AnalyzePage() {
  const dispatch = useAppDispatch();
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [propId, setPropId] = useState("");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const submitMutation = useMutation({
    mutationFn: propertyApi.analyzeProperty,
    onSuccess: (job) => {
      setActiveJobId(job.job_id);
      dispatch(upsertJob(job));
    },
  });

  const { data: job } = useQuery({
    queryKey: ["job", activeJobId],
    queryFn: () => propertyApi.getJob(activeJobId!),
    enabled: !!activeJobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "processing" ? 2000 : false;
    },
  });

  // Push every job update into Redux so Dashboard and Jobs pages reflect it
  useEffect(() => {
    if (job) dispatch(upsertJob(job));
  }, [job, dispatch]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const latitude = parseFloat(lat);
    const longitude = parseFloat(lon);
    if (isNaN(latitude) || isNaN(longitude)) return;
    setActiveJobId(null);
    submitMutation.mutate({ latitude, longitude, property_id: propId || undefined });
  };

  const isRunning = job?.status === "queued" || job?.status === "processing";

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Analyze Property</h1>
        <p className="text-gray-500 text-sm mt-1">Enter coordinates to run full AI due diligence pipeline</p>
      </div>

      {/* Input Form */}
      <Card>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">Latitude</label>
              <input
                type="number"
                step="any"
                placeholder="e.g. 25.7617"
                value={lat}
                onChange={(e) => setLat(e.target.value)}
                required
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">Longitude</label>
              <input
                type="number"
                step="any"
                placeholder="e.g. -80.1918"
                value={lon}
                onChange={(e) => setLon(e.target.value)}
                required
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1.5">Property ID (optional)</label>
              <input
                type="text"
                placeholder="e.g. PROP-001"
                value={propId}
                onChange={(e) => setPropId(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>
          </div>
          <button
            type="submit"
            disabled={submitMutation.isPending || isRunning}
            className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 disabled:text-blue-600 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {submitMutation.isPending ? <Spinner size="sm" /> : <Search className="w-4 h-4" />}
            {submitMutation.isPending ? "Submitting…" : "Run Analysis"}
          </button>
        </form>
      </Card>

      {/* Job Status */}
      {job && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <MapPin className="w-4 h-4 text-gray-500" />
              <span className="text-sm text-gray-400">
                {job.latitude.toFixed(6)}, {job.longitude.toFixed(6)}
              </span>
              {job.property_id && (
                <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">{job.property_id}</span>
              )}
            </div>
            <div className="flex items-center gap-3">
              <StatusBadge status={job.status} />
              {isRunning && <Spinner size="sm" />}
            </div>
          </div>

          {/* Pipeline stages */}
          {job.stages.length > 0 && (
            <Card>
              <h3 className="text-sm font-semibold text-gray-300 mb-3">Pipeline Progress</h3>
              <PipelineProgress stages={job.stages} />
            </Card>
          )}

          {/* Images */}
          {job.status === "completed" && (
            <Card>
              <h3 className="text-sm font-semibold text-gray-300 mb-3">Captured Images</h3>
              <ImageGallery jobId={job.job_id} />
            </Card>
          )}

          {/* Result */}
          {job.result && <AnalysisResultCard result={job.result} />}

          {/* Error */}
          {job.status === "failed" && job.error && (
            <Card className="border-red-900/50 bg-red-950/20">
              <p className="text-sm text-red-400">Pipeline failed: {job.error}</p>
            </Card>
          )}

          {/* Report links */}
          {job.status === "completed" && (
            <div className="flex gap-3">
              <a
                href={`/reports/${job.job_id}_report.html`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-blue-400 hover:text-blue-300 underline underline-offset-2"
              >
                View HTML Report
              </a>
              <a
                href={`/reports/${job.job_id}_report.json`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-blue-400 hover:text-blue-300 underline underline-offset-2"
              >
                Download JSON
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
