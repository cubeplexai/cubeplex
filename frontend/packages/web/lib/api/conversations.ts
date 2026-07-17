/**
 * Web-package surface for conversation message-send types.
 *
 * The actual `POST /api/v1/conversations/{id}/messages` call lives in
 * `@cubeplex/core` (``packages/core/src/api/stream.ts``). This file re-exports
 * the request body type so web call sites have a single import path that
 * matches the plan's structure (Task F3) and so future web-only fields can
 * be layered on without round-tripping through `@cubeplex/core`.
 */

export type { SendMessageRequest } from '@cubeplex/core'
