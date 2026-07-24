/**
 * `analysisStore` is the single zustand store for the workbench UI.
 *
 * It owns the same `AnalysisState` shape the rest of the app reads
 * from. Every mutation goes through the pure `analysisReducer` — the
 * React components never write to `phase` / `requirement` / etc. directly.
 *
 * Why a single store (vs many)? The workbench reducer is a pure
 * transition function over a small state object. A single store keeps
 * that invariant simple; cross-store coordination (e.g. "selecting a
 * session also resets the report version list") lives in the reducer
 * and would be hard to reason about across multiple stores.
 */
import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import {
  analysisReducer,
  initialAnalysisState,
  isBusyPhase,
  type AnalysisState,
  type AnalysisAction,
} from './analysisReducer'

interface AnalysisStore extends AnalysisState {
  /** Pure dispatcher; pass any AnalysisAction. */
  dispatch: (action: AnalysisAction) => void

  /** Convenience selectors. */
  isBusy: () => boolean
  reset: () => void
}

export const useAnalysisStore = create<AnalysisStore>()(
  immer((set) => ({
    ...initialAnalysisState,
    dispatch: (action) =>
      set((draft) => {
        const next = analysisReducer(draft as AnalysisState, action)
        // `next` is the new immutable state from the reducer. Copy the
        // scalar fields into the immer draft so subscribers fire.
        draft.phase = next.phase
        draft.activeSessionId = next.activeSessionId
        draft.sessions = next.sessions
        draft.requirement = next.requirement
        draft.reportVersions = next.reportVersions
        draft.selectedReportVersion = next.selectedReportVersion
        draft.timeline = next.timeline
        draft.error = next.error
      }),
    isBusy: () => isBusyPhase(useAnalysisStore.getState().phase),
    reset: () => set(() => ({ ...initialAnalysisState })),
  })),
)
