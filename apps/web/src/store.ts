import { create } from "zustand";

interface RunSelectionState {
  selectedRunId: string | null;
  setSelectedRunId: (runId: string | null) => void;
}

const STORAGE_KEY = "veridix.selectedRunId";

function readInitialRunId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export const useRunSelection = create<RunSelectionState>((set) => ({
  selectedRunId: readInitialRunId(),
  setSelectedRunId: (runId) => {
    if (typeof window !== "undefined") {
      try {
        if (runId) {
          window.sessionStorage.setItem(STORAGE_KEY, runId);
        } else {
          window.sessionStorage.removeItem(STORAGE_KEY);
        }
      } catch {
        // Storage can be unavailable in private/embedded contexts.
      }
    }
    set({ selectedRunId: runId });
  },
}));
