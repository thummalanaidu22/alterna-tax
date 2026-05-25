import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Clock, ChevronDown, ChevronUp } from "lucide-react";
import { propertyApi } from "../services/api";
import type { PropertyJob } from "../types/property";

function ConfidenceBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 80 ? "text-green-400" : pct >= 65 ? "text-amber-400" : "text-red-400";
  return <span className={`font-mono text-sm ${color}`}>{pct}%</span>;
}

function ReviewCard({
  job,
  onVerdict,
}: {
  job: PropertyJob;
  onVerdict: (jobId: string, verdict: "approved" | "rejected", notes: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const result = job.result!;

  async function submit(verdict: "approved" | "rejected") {
    setSubmitting(true);
    onVerdict(job.job_id, verdict, notes);
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 flex items-center gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-white truncate">
              {job.property_id ?? job.job_id.slice(0, 12)}
            </span>
            <span className="text-xs text-gray-500 font-mono">
              {job.latitude.toFixed(5)}, {job.longitude.toFixed(5)}
            </span>
          </div>
          <div className="flex items-center gap-3 mt-1">
            <span className="text-xs text-gray-400 capitalize">
              {result.property_type.replace(/_/g, " ")}
            </span>
            <span className="text-gray-700">·</span>
            <ConfidenceBadge score={result.confidence_score} />
            <span className="text-gray-700">·</span>
            <span className="text-xs text-gray-500">
              {new Date(job.created_at).toLocaleString()}
            </span>
          </div>
        </div>

        {/* Quick verdict buttons */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            disabled={submitting}
            onClick={() => submit("approved")}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-600/20 text-green-400 hover:bg-green-600/30 text-sm font-medium transition-colors disabled:opacity-50"
          >
            <CheckCircle className="w-4 h-4" />
            Approve
          </button>
          <button
            disabled={submitting}
            onClick={() => submit("rejected")}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 text-sm font-medium transition-colors disabled:opacity-50"
          >
            <XCircle className="w-4 h-4" />
            Reject
          </button>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* AI flags */}
      <div className="px-5 pb-3 flex flex-wrap gap-2">
        {result.human_review_reasons.map((reason, i) => (
          <span
            key={i}
            className="text-xs bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded px-2 py-0.5"
          >
            {reason}
          </span>
        ))}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-800 px-5 py-4 space-y-4">
          <p className="text-sm text-gray-400">{result.summary}</p>

          <div>
            <label className="block text-xs text-gray-500 mb-1.5">
              Reviewer notes (optional)
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="Add context for the audit trail..."
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 resize-none focus:outline-none focus:border-blue-500"
            />
          </div>

          <div className="flex gap-2">
            <button
              disabled={submitting}
              onClick={() => submit("approved")}
              className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg bg-green-600/20 text-green-400 hover:bg-green-600/30 text-sm font-medium transition-colors disabled:opacity-50"
            >
              <CheckCircle className="w-4 h-4" />
              Approve with notes
            </button>
            <button
              disabled={submitting}
              onClick={() => submit("rejected")}
              className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 text-sm font-medium transition-colors disabled:opacity-50"
            >
              <XCircle className="w-4 h-4" />
              Reject with notes
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function ReviewPage() {
  const queryClient = useQueryClient();

  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["review-queue"],
    queryFn: () => propertyApi.getReviewQueue(),
    refetchInterval: 15_000,
  });

  const mutation = useMutation({
    mutationFn: ({
      jobId,
      verdict,
      notes,
    }: {
      jobId: string;
      verdict: "approved" | "rejected";
      notes: string;
    }) => propertyApi.reviewJob(jobId, { verdict, notes }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      queryClient.invalidateQueries({ queryKey: ["jobs-map"] });
    },
  });

  function handleVerdict(jobId: string, verdict: "approved" | "rejected", notes: string) {
    mutation.mutate({ jobId, verdict, notes });
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Review Queue</h1>
          <p className="text-sm text-gray-500">
            Properties flagged for human review — awaiting underwriter verdict
          </p>
        </div>
        <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-1.5">
          <Clock className="w-4 h-4 text-amber-400" />
          <span className="text-sm text-amber-400 font-medium">{jobs.length} pending</span>
        </div>
      </div>

      {isLoading && (
        <div className="flex justify-center py-12">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {!isLoading && jobs.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <CheckCircle className="w-12 h-12 text-green-500/40 mb-3" />
          <p className="text-gray-400 font-medium">Review queue is empty</p>
          <p className="text-sm text-gray-600 mt-1">
            All NEEDS_HUMAN_REVIEW jobs have been verdicted.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {jobs.map((job) => (
          <ReviewCard key={job.job_id} job={job} onVerdict={handleVerdict} />
        ))}
      </div>
    </div>
  );
}
