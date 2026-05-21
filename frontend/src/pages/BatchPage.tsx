import { useMutation, useQuery } from "@tanstack/react-query";
import { Upload, Plus, Trash2, PlayCircle, Download } from "lucide-react";
import * as XLSX from "xlsx";
import { useDispatch, useSelector } from "react-redux";
import { useRef } from "react";
import { propertyApi } from "../services/api";
import type { PropertyRequest } from "../types/property";
import type { RootState } from "../store";
import {
  setBatchRows,
  addBatchRow,
  removeBatchRow,
  updateBatchRow,
  setBatchId,
  type BatchRow,
} from "../store/jobsSlice";
import { Card } from "../components/ui/Card";
import { Spinner } from "../components/ui/Spinner";
import { StatusBadge } from "../components/ui/StatusBadge";
import { DecisionBadge } from "../components/ui/DecisionBadge";

export function BatchPage() {
  const dispatch = useDispatch();
  const rows = useSelector((s: RootState) => s.jobs.batchRows);
  const batchId = useSelector((s: RootState) => s.jobs.batchId);
  const submittingRef = useRef(false);

  const submitMutation = useMutation({
    mutationFn: propertyApi.analyzeBatch,
    onSuccess: (res) => dispatch(setBatchId(res.batch_id)),
    onSettled: () => { submittingRef.current = false; },
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

  const parseCSVRow = (text: string): string[][] => {
    const rows: string[][] = [];
    let row: string[] = [];
    let field = "";
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      const next = text[i + 1];
      if (inQuotes) {
        if (ch === '"' && next === '"') { field += '"'; i++; }
        else if (ch === '"') { inQuotes = false; }
        else { field += ch; }
      } else {
        if (ch === '"') { inQuotes = true; }
        else if (ch === ',') { row.push(field); field = ""; }
        else if (ch === '\n' || ch === '\r') {
          row.push(field); field = "";
          if (row.some((f) => f.length > 0)) rows.push(row);
          row = [];
          if (ch === '\r' && next === '\n') i++;
        } else { field += ch; }
      }
    }
    if (field || row.length > 0) { row.push(field); if (row.some((f) => f.length > 0)) rows.push(row); }
    return rows;
  };

  const handleCSV = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      const parsed = parseCSVRow(text);
      if (parsed.length < 2) return;

      const headers = parsed[0].map((h) => h.toLowerCase().trim());
      const latIdx = headers.findIndex((h) => h === "latitude");
      const lngIdx = headers.findIndex((h) => h === "longitude");
      const mapIdx = headers.findIndex((h) => h === "googlemap");
      const propIdIdx = headers.findIndex((h) => ["parcelid", "propertyappraiserformat", "property_id", "id"].includes(h));

      const newRows: BatchRow[] = parsed
        .slice(1)
        .map((parts) => {
          let lat = latIdx >= 0 ? parts[latIdx]?.trim() ?? "" : "";
          let lng = lngIdx >= 0 ? parts[lngIdx]?.trim() ?? "" : "";

          if ((!lat || !lng) && mapIdx >= 0) {
            const match = (parts[mapIdx] ?? "").match(/query=(-?\d+\.?\d*),(-?\d+\.?\d*)/);
            if (match) { lat = lat || match[1]; lng = lng || match[2]; }
          }

          const propId = propIdIdx >= 0 ? parts[propIdIdx]?.trim() ?? "" : "";
          return { id: crypto.randomUUID(), latitude: lat, longitude: lng, property_id: propId };
        })
        .filter((r) => r.latitude && r.longitude);

      if (newRows.length > 0) dispatch(setBatchRows(newRows));
    };
    reader.readAsText(file);
  };

  const handleSubmit = () => {
    if (submittingRef.current || submitMutation.isPending) return;
    submittingRef.current = true;

    const properties: PropertyRequest[] = rows
      .filter((r) => r.latitude && r.longitude)
      .map((r) => ({
        latitude: parseFloat(r.latitude),
        longitude: parseFloat(r.longitude),
        property_id: r.property_id || undefined,
      }));
    if (properties.length === 0) { submittingRef.current = false; return; }
    dispatch(setBatchId(null));
    submitMutation.mutate({ properties });
  };

  const validRows = rows.filter((r) => r.latitude && r.longitude).length;

  const buildExportRows = () =>
    (batchStatus?.jobs ?? []).map((job) => ({
      Property: job.property_id || job.job_id,
      Latitude: job.latitude,
      Longitude: job.longitude,
      Status: job.status,
      Decision: job.result?.decision ?? "",
      Confidence: job.result ? `${Math.round(job.result.confidence_score * 100)}%` : "",
      PropertyType: job.result?.property_type ?? "",
      RejectionReasons: job.result?.rejection_reasons?.join("; ") ?? "",
      ReviewReasons: job.result?.human_review_reasons?.join("; ") ?? "",
      Summary: job.result?.summary ?? "",
    }));

  const downloadCSV = () => {
    const data = buildExportRows();
    if (data.length === 0) return;
    const headers = Object.keys(data[0]);
    const csvLines = [
      headers.join(","),
      ...data.map((row) =>
        headers.map((h) => `"${String(row[h as keyof typeof row]).replace(/"/g, '""')}"`).join(",")
      ),
    ];
    const blob = new Blob([csvLines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `batch_results_${batchId?.slice(0, 8) ?? "export"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadXLSX = () => {
    const data = buildExportRows();
    if (data.length === 0) return;
    const ws = XLSX.utils.json_to_sheet(data);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Results");
    XLSX.writeFile(wb, `batch_results_${batchId?.slice(0, 8) ?? "export"}.xlsx`);
  };

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
                  onChange={(e) => dispatch(updateBatchRow({ id: row.id, field, value: e.target.value }))}
                  className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
                />
              ))}
              <button onClick={() => dispatch(removeBatchRow(row.id))} className="text-gray-700 hover:text-red-400 transition-colors">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>

        <div className="flex items-center gap-3 mt-4 pt-4 border-t border-gray-800">
          <button onClick={() => dispatch(addBatchRow())} className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors">
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

          {/* Download buttons */}
          <div className="flex items-center justify-end gap-2">
            <button
              onClick={downloadCSV}
              className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 text-xs font-medium rounded-lg transition-colors"
            >
              <Download className="w-3.5 h-3.5" /> Download CSV
            </button>
            <button
              onClick={downloadXLSX}
              className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 text-xs font-medium rounded-lg transition-colors"
            >
              <Download className="w-3.5 h-3.5" /> Download XLSX
            </button>
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
