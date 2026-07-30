import { useAuthStore } from '../stores/authStore'

/**
 * P-2: 统一的 401 处理。token 过期时后端返回 401——此前各客户端只 toast
 * 「failed: 401」却不登出/跳转，应用带着过期 token 继续渲染陈旧数据。
 * 现在统一登出并跳登录页。
 */
export function handleUnauthorized(): void {
  try {
    useAuthStore.getState().logout()
  } catch {
    // authStore 不可用（如测试环境）时，至少清掉本地 token。
    try {
      localStorage.removeItem('ragent_auth')
    } catch {
      /* ignore */
    }
  }
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}
