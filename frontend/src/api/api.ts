import { useAuthStore } from '../stores/authStore'

let baseUrl = ''

export function setBaseUrl(url: string) {
  baseUrl = url
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { token } = useAuthStore.getState()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${baseUrl}${path}`, { ...options, headers })

  if (res.status === 401) {
    useAuthStore.getState().logout()
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }

  return res.json()
}

export async function loginAPI(username: string, password: string) {
  return request<{ access_token: string; user_id: number; username: string }>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export async function fetchSessions() {
  return request<{ sessions: Array<{ session_id: string; msg_count: number; first_message: string; last_message: string }> }>('/api/v1/sessions')
}

export async function fetchConversations(sessionId: string) {
  return request<{ messages: Array<{ id: number; role: string; content: string | null; message_type: string; metadata: unknown; created_at: string }> }>(`/api/v1/conversations/${sessionId}`)
}
