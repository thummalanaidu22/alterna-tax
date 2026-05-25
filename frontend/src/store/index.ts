import { configureStore } from "@reduxjs/toolkit";
import jobsReducer from "./jobsSlice";
import uiReducer from "./uiSlice";

export const store = configureStore({
  reducer: {
    jobs: jobsReducer,
    ui: uiReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
