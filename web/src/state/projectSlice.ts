// web/src/state/projectSlice.ts
import { create } from 'zustand'

export interface ProjectState {
  activeProjectId: string | null
  setActiveProjectId(id: string | null): void
}

export const useProjectStore = create<ProjectState>((set) => ({
  activeProjectId: null,
  setActiveProjectId(id) {
    set({ activeProjectId: id })
  },
}))

export function resetProjectStore(): void {
  useProjectStore.setState({ activeProjectId: null }, false)
}
