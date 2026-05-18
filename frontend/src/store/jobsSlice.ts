import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { PropertyJob, BatchJobStatus } from "../types/property";

interface JobsState {
  recentJobs: PropertyJob[];
  activeBatch: BatchJobStatus | null;
  selectedJobId: string | null;
}

const initialState: JobsState = {
  recentJobs: [],
  activeBatch: null,
  selectedJobId: null,
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
  },
});

export const { setRecentJobs, upsertJob, setActiveBatch, selectJob } = jobsSlice.actions;
export default jobsSlice.reducer;
