import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

/**
 * AuthGuard redirects unauthenticated users to /login.
 * It reads the token from the zustand `authStore` (persisted in
 * localStorage by that store's `persist` middleware).
 */
export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const location = useLocation()

  let token: string | null = null
  try {
    const raw = localStorage.getItem('ragent_auth')
    if (raw) {
      const parsed = JSON.parse(raw)
      token = parsed?.state?.token ?? null
    }
  } catch {
    /* ignore */
  }

  useEffect(() => {
    // No-op for now; placeholder for future token-refresh hooks.
  }, [token])

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return <>{children}</>
}
