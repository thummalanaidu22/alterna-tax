import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { PropertyJob, BatchJobStatus } from "../types/property";

export interface BatchRow {
  id: string;
  latitude: string;
  longitude: string;
  property_id: string;
}

interface JobsState {
  recentJobs: PropertyJob[];
  activeBatch: BatchJobStatus | null;
  selectedJobId: string | null;
  batchRows: BatchRow[];
  batchId: string | null;
}

const initialState: JobsState = {
  recentJobs: [],
  activeBatch: null,
  selectedJobId: null,
  batchRows: [{ id: "default", latitude: "", longitude: "", property_id: "" }],
  batchId: null,
};

const jobsSlice = createSlice({
  name: "jobs",
  initialState,
  reducers: {
    setRecentJobs(state, action: PayloadAction<PropertyJob[]>) {
      state.recentJobs = action.payload;
    },
    upsertJob(state, action: PayloadAction<PropertyJob>) {
      const idx = state.recentJobs.findIndex((j) => j.job_id === action.payload.job_id);
      if (idx >= 0) {
        state.recentJobs[idx] = action.payload;
      } else {
        state.recentJobs.unshift(action.payload);
      }
    },
    setActiveBatch(state, action: PayloadAction<BatchJobStatus | null>) {
      state.activeBatch = action.payload;
    },
    selectJob(state, action: PayloadAction<string | null>) {
      state.selectedJobId = action.payload;
    },
    setBatchRows(state, action: PayloadAction<BatchRow[]>) {
      state.batchRows = action.payload;
    },
    addBatchRow(state) {
      state.batchRows.push({ id: crypto.randomUUID(), latitude: "", longitude: "", property_id: "" });
    },
    removeBatchRow(state, action: PayloadAction<string>) {
      state.batchRows = state.batchRows.filter((r) => r.id !== action.payload);
    },
    updateBatchRow(state, action: PayloadAction<{ id: string; field: keyof BatchRow; value: string }>) {
      const row = state.batchRows.find((r) => r.id === action.payload.id);
      if (row) row[action.payload.field] = action.payload.value;
    },
    setBatchId(state, action: PayloadAction<string | null>) {
      state.batchId = action.payload;
    },
  },
});

export const {
  setRecentJobs,
  upsertJob,
  setActiveBatch,
  selectJob,
  setBatchRows,
  addBatchRow,
  removeBatchRow,
  updateBatchRow,
  setBatchId,
} = jobsSlice.actions;
export default jobsSlice.reducer;
