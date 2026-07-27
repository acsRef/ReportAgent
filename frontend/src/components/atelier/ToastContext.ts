import { createContext } from 'react'

export interface ToastContextValue {
  success: (msg: string) => void
  error: (msg: string) => void
  warning: (msg: string) => void
  info: (msg: string) => void
}

export const ToastContext = createContext<ToastContextValue | null>(null)
