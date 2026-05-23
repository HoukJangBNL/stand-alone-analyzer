// web/src/state/projectSlice.ts
import { create } from 'zustand'

export interface ProjectState {
  activeProjectId: string | null
  activeScanId: number | null
  setActiveProjectId(id: string | null): void
  setActiveScanId(id: number | null): void
}

export const useProjectStore = create<ProjectState>((set) => ({
  activeProjectId: null,
  activeScanId: null,
  setActiveProjectId(id) {
    // Project change ⇒ active scan no longer valid (scans belong to a project).
    set((s) => {
      if (s.activeProjectId === id) return { activeProjectId: id }
      return { activeProjectId: id, activeScanId: null }
    })
  },
  setActiveScanId(id) {
    set({ activeScanId: id })
  },
}))

export function resetProjectStore(): void {
  useProjectStore.setState(
    { activeProjectId: null, activeScanId: null },
    false
  )
}
