import {
  useCallback,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { ToastContext } from './ToastContext'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface ToastItem {
  id: number
  message: string
  type: ToastType
}

let nextId = 0

const TONE_MAP: Record<ToastType, string> = {
  success: 'is-green',
  error: 'is-red',
  warning: 'is-amber',
  info: 'is-teal',
}

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const remove = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
    const timer = timersRef.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timersRef.current.delete(id)
    }
  }, [])

  const add = useCallback(
    (message: string, type: ToastType) => {
      const id = nextId++
      setToasts(prev => [...prev, { id, message, type }])
      const timer = setTimeout(() => remove(id), 3000)
      timersRef.current.set(id, timer)
    },
    [remove],
  )

  const success = useCallback((msg: string) => add(msg, 'success'), [add])
  const error = useCallback((msg: string) => add(msg, 'error'), [add])
  const warning = useCallback((msg: string) => add(msg, 'warning'), [add])
  const info = useCallback((msg: string) => add(msg, 'info'), [add])

  return (
    <ToastContext.Provider value={{ success, error, warning, info }}>
      {children}
      {createPortal(
        <div className="atelier-toast-root" aria-live="polite" role="status">
          {toasts.map(t => (
            <div key={t.id} className={`atelier-toast is-show ${TONE_MAP[t.type]}`}>
              {t.message}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

export { ToastProvider }
