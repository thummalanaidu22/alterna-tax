import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Upload, Plus, Trash2, PlayCircle } from "lucide-react";
import { propertyApi } from "../services/api";
import type { PropertyRequest } from "../types/property";
import { Card } from "../components/ui/Card";
import { Spinner } from "../components/ui/Spinner";
import { StatusBadge } from "../components/ui/StatusBadge";
import { DecisionBadge } from "../components/ui/DecisionBadge";

interface RowInput {
  id: string;
  latitude: string;
  longitude: string;
  property_id: string;
}

function newRow(): RowInput {
  return { id: crypto.randomUUID(), latitude: "", longitude: "", property_id: "" };
}

export function BatchPage() {
  const [rows, setRows] = useState<RowInput[]>([newRow()]);
  const [batchId, setBatchId] = useState<string | null>(null);

  const submitMutation = useMutation({
    mutationFn: propertyApi.analyzeBatch,
    onSuccess: (res) => setBatchId(res.batch_id),
  });

  const { data: batchStatus } = useQuery({
    queryKey: ["batch", batchId],
    queryFn: () => propertyApi.getBatchStatus(batchId!),
    enabled: !!batchId,
    refetchInterval: (query) => {
      const d = query.state.data;
      if (!d) return 2000;
      return d.queued > 0 || d.processing > 0 ? 2000 : false;
    },
  });

  const addRow = () => setRows((prev) => [...prev, newRow()]);
  const removeRow = (id: string) => setRows((prev) => prev.filter((r) => r.id !== id));
  const updateRow = (id: string, field: keyof RowInput, value: string) =>
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, [field]: value } : r)));

  const handleCSV = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      const lines = text.split("\n").filter(Boolean);
      const newRows: RowInput[] = lines.slice(1).map((line) => {
        const parts = line.split(",").map((p) => p.trim());
        return { id: crypto.randomUUID(), latitude: parts[0] || "", longitude: parts[1] || "", property_id: parts[2] || "" };
      });
      if (newRows.length > 0) setRows(newRows);
    };
    reader.readAsText(file);
  };

  const handleSubmit = () => {
    const properties: PropertyRequest[] = rows
      .filter((r) => r.latitude && r.longitude)
      .map((r) => ({
        latitude: parseFloat(r.latitude),
        longitude: parseFloat(r.longitude),
        property_id: r.property_id || undefined,
      }));
    if (properties.length === 0) return;
    setBatchId(null);
    submitMutation.mutate({ properties });
  };

  const validRows = rows.filter((r) => r.latitude && r.longitude).length;

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Batch Analysis</h1>
        <p className="text-gray-500 text-sm mt-1">Analyze multiple properties simultaneously</p>
      </div>

      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-300">Properties ({validRows} valid)</h2>
          <label className="flex items-center gap-2 text-xs text-blue-400 hover:text-blue-300 cursor-pointer">
            <Upload className="w-3.5 h-3.5" />
            Import CSV
            <input type="file" accept=".csv" className="hidden" onChange={handleCSV} />
          </label>
        </div>

        <div className="text-xs text-gray-600 mb-2 px-1 grid grid-cols-[1fr_1fr_1fr_32px] gap-2">
          <span>Latitude</span><span>Longitude</span><span>Property ID</span><span />
        </div>

        <div className="space-y-2 max-h-72 overflow-y-auto">
          {rows.map((row) => (
            <div key={row.id} className="grid grid-cols-[1fr_1fr_1fr_32px] gap-2 items-center">
              {(["latitude", "longitude", "property_id"] as const).map((field) => (
                <input
                  key={field}
                  type={field === "property_id" ? "text" : "number"}
                  step="any"
                  placeholder={field === "property_id" ? "optional" : field}
                  value={row[field]}
                  onChange={(e) => updateRow(row.id, field, e.target.value)}
                  className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
                />
              ))}
              <button onClick={() => removeRow(row.id)} className="text-gray-700 hover:text-red-400 transition-colors">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>

        <div className="flex items-center gap-3 mt-4 pt-4 border-t border-gray-800">
          <button onClick={addRow} className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors">
            <Plus className="w-3.5 h-3.5" /> Add Row
          </button>
          <button
            onClick={handleSubmit}
            disabled={validRows === 0 || submitMutation.isPending}
            className="ml-auto flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 disabled:text-blue-600 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {submitMutation.isPending ? <Spinner size="sm" /> : <PlayCircle className="w-4 h-4" />}
            {submitMutation.isPending ? "Submitting…" : `Run ${validRows} Properties`}
          </button>
        </div>
      </Card>

      {/* Batch Results */}
      {batchStatus && (
        <div className="space-y-4">
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: "Total", val: batchStatus.total, color: "text-white" },
              { label: "Completed", val: batchStatus.completed, color: "text-emerald-400" },
              { label: "Processing", val: batchStatus.processing + batchStatus.queued, color: "text-blue-400" },
              { label: "Failed", val: batchStatus.failed, color: "text-red-400" },
            ].map((s) => (
              <Card key={s.label} className="text-center py-3">
                <div className={`text-2xl font-bold ${s.color}`}>{s.val}</div>
                <div className="text-xs text-gray-500 mt-1">{s.label}</div>
              </Card>
            ))}
          </div>

          {/* Progress bar */}
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 transition-all duration-500"
              style={{ width: `${batchStatus.total ? ((batchStatus.completed + batchStatus.failed) / batchStatus.total) * 100 : 0}%` }}
            />
          </div>

          {/* Jobs table */}
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-800">
                    <th className="text-left py-2 px-3">Property</th>
                    <th className="text-left py-2 px-3">Coordinates</th>
                    <th className="text-left py-2 px-3">Status</th>
                    <th className="text-left py-2 px-3">Decision</th>
                    <th className="text-left py-2 px-3">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {batchStatus.jobs.map((job) => (
                    <tr key={job.job_id} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                      <td className="py-2 px-3 text-gray-400">{job.property_id || job.job_id.slice(0, 8)}</td>
                      <td className="py-2 px-3 text-gray-500 font-mono text-xs">
                        {job.latitude.toFixed(4)}, {job.longitude.toFixed(4)}
                      </td>
                      <td className="py-2 px-3"><StatusBadge status={job.status} /></td>
                      <td className="py-2 px-3">
                        {job.result ? <DecisionBadge decision={job.result.decision} /> : <span className="text-gray-700">—</span>}
                      </td>
                      <td className="py-2 px-3 text-gray-400">
                        {job.result ? `${Math.round(job.result.confidence_score * 100)}%` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
