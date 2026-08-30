/**
 * Legacy SSE adapter.
 *
 * Some clients (e.g. the still-present `ChatCards` component) understand
 * the old `event: card | clarify | token` shape. When `mode=legacy` is
 * used at /api/v1/chat, the backend may emit these. This adapter maps
 * them into a `legacy` channel payload so the reducer can ignore them
 * while the legacy components can still render.
 *
 * 注意：该形状独立于 api/analysisEvents 的 canonical union——新客户端按
 * frontend-contract 只消费公开事件契约，legacy 事件被 parser 丢弃，此处
 * 仅服务仍存续的旧组件（P15 随 legacy 一并删除）。
 */

export interface LegacyEvent {
  type: 'legacy'
  data: { event: string; payload: any }
}

export function adaptLegacyEvent(
  eventName: string,
  data: any,
): LegacyEvent {
  return { type: 'legacy', data: { event: eventName, payload: data } }
}
