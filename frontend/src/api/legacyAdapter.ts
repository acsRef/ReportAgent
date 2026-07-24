/**
 * Legacy SSE adapter.
 *
 * Some clients (e.g. the still-present `ChatCards` component) understand
 * the old `event: card | clarify | token` shape. When `mode=legacy` is
 * used at /api/v1/chat, the backend may emit these. This adapter maps
 * them into a `legacy` channel payload so the reducer can ignore them
 * while the legacy components can still render.
 *
 * It is also used as the default fallback when the parser can't classify
 * an incoming event (defensive).
 */
import type { AnalysisStreamEvent } from './analysisClient'

export function adaptLegacyEvent(
  eventName: string,
  data: any,
): AnalysisStreamEvent {
  return { type: 'legacy', data: { event: eventName, payload: data } }
}
