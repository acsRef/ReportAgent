import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AuthStore {
  token: string | null
  userId: number | null
  username: string | null
  setAuth: (token: string, userId: number, username: string) => void
  logout: () => void
  isAuthenticated: () => boolean
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set, get) => ({
      token: null,
      userId: null,
      username: null,

      setAuth: (token: string, userId: number, username: string) => set({ token, userId, username }),

      logout: () => set({ token: null, userId: null, username: null }),

      isAuthenticated: () => !!get().token,
    }),
    { name: 'ragent_auth' },
  ),
)
