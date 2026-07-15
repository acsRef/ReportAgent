import type { DashboardConfig, DashboardTemplate } from '../types/dashboard';
import type { DashboardGenerateRequest, DashboardGenerateResponse } from '../types/api';

const API_BASE = '/api/v1';

export async function generateDashboard(
  request: DashboardGenerateRequest
): Promise<DashboardGenerateResponse> {
  const res = await fetch(`${API_BASE}/dashboard/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`生成看板失败: ${res.status}`);
  return res.json();
}

export async function getTemplates(): Promise<DashboardTemplate[]> {
  const res = await fetch(`${API_BASE}/dashboard/templates`);
  if (!res.ok) throw new Error(`获取模板失败: ${res.status}`);
  const data = await res.json();
  return data.templates;
}

export async function saveDashboard(config: DashboardConfig): Promise<void> {
  const res = await fetch(`${API_BASE}/dashboard/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error(`保存看板失败: ${res.status}`);
}

export async function loadDashboard(id: string): Promise<DashboardConfig> {
  const res = await fetch(`${API_BASE}/dashboard/${id}`);
  if (!res.ok) throw new Error(`加载看板失败: ${res.status}`);
  return res.json();
}