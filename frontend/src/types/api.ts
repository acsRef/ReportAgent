import type { DashboardConfig } from './dashboard';

/** 聊天请求 */
export interface ChatRequest {
  user_query: string;
  session_id: string;
}

/** 看板生成请求 */
export interface DashboardGenerateRequest {
  user_requirement: string;
  template_id?: string;
}

/** 看板生成响应 */
export interface DashboardGenerateResponse {
  dashboard: DashboardConfig;
  session_id: string;
}