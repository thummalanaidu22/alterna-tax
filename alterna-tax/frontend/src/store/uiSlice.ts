import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

type Tab = "dashboard" | "analyze" | "batch" | "jobs";

interface UIState {
  activeTab: Tab;
  sidebarOpen: boolean;
  ollamaConnected: boolean | null;
}

const initialState: UIState = {
  activeTab: "dashboard",
  sidebarOpen: true,
  ollamaConnected: null,
};

const uiSlice = createSlice({
  name: "ui",
  initialState,
  reducers: {
    setActiveTab(state, action: PayloadAction<Tab>) {
      state.activeTab = action.payload;
    },
    toggleSidebar(state) {
      state.sidebarOpen = !state.sidebarOpen;
    },
    setOllamaConnected(state, action: PayloadAction<boolean>) {
      state.ollamaConnected = action.payload;
    },
  },
});

export const { setActiveTab, toggleSidebar, setOllamaConnected } = uiSlice.actions;
export default uiSlice.reducer;
