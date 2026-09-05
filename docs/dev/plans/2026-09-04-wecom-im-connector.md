# WeCom IM Connector Implementation Plan

**Goal:** Add a text-first WeCom AI Bot connector that receives direct and group messages over
WebSocket and returns CubePlex agent runs through WeCom's native reply stream.

**Architecture:** A `WecomGateway` owns the authenticated socket, request correlation, heartbeat,
and reconnect loop for one stored bot account. It normalizes each callback into the existing IM
inbound transaction, persisting the callback `req_id` as `reply_to_id`; a registered
`WecomPlatform` later builds a `WecomOpDispatcher` that uses that value for cumulative stream
frames and uses proactive sends when no passive reply is available. Account storage, identity
links, conversation routing, run queues, leases, and runtime aggregation remain shared.

**Tech stack:** FastAPI/Pydantic, asyncio + aiohttp, SQLModel/PostgreSQL, Redis gateway leases,
pytest, Next.js/React 19, strict TypeScript, next-intl. Add `aiohttp` as a direct backend
dependency with `uv add aiohttp`; do not rely on its transitive installation through another IM
SDK.

## Unit 1 — Normalize WeCom callbacks and expose the connector seam

**Files:**

- Create `backend/cubeplex/im/wecom/__init__.py` to register the platform.
- Create `backend/cubeplex/im/wecom/connector.py` for callback parsing, passive/proactive text
  sends, identity rejection replies, and the no-native-file connector methods required by the
  existing outbound artifact pipeline.
- Create `backend/tests/unit/im/wecom/__init__.py` and
  `backend/tests/unit/im/wecom/test_connector.py` for pure parsing and formatting contracts.

**Interfaces:**

- `WecomConnector.parse_inbound(raw: dict[str, Any], *, binding_mode: BindingMode =
  "isolated") -> InboundEvent | None` consumes the full WebSocket frame so both
  `headers.req_id` and `body` are available.
- The connector is constructed with a live gateway plus optional bound `chat_id` and
  `reply_req_id` for outbound operations.
- `send_to_chat(chat_id, reply_to_id, text) -> str | None` uses a passive markdown reply when a
  request ID exists and otherwise asks the gateway for a proactive send.
- Ingress pairs the connector's rejection notifier with the shared `NullIdentityResolver`;
  the WeCom connector does not pretend that the AI Bot protocol can resolve email addresses.
- `send_file`, `send_image`, and `upload_image` return their existing unsupported values so
  artifacts fall back to share links.

**Core logic:**

- Accept `aibot_msg_callback` and legacy `aibot_callback` frames with a stable `msgid`, sender
  `userid`, and WebSocket `req_id`.
- Extract text, mixed-message text parts, WeCom voice transcription, and text/voice quote
  fallback. Drop media-only and unknown messages in v1.
- Map direct messages to `dm`; map groups to `u:{userid}` in isolated mode and `ch` in shared
  mode. Persist the frame `req_id` as `reply_to_id` and the WeCom `msgid` as both receipt and
  inbound-message IDs.
- Strip only a leading group mention. Never log the full callback or credential content.
- Format proactive output into readable chunks no longer than 4,000 characters.

**Tests:**

- Protect DM, isolated-group, and shared-group scope mapping.
- Protect `req_id` versus `msgid` field roles so an implementation cannot silently replace the
  passive reply handle with the receipt ID.
- Protect mixed/voice/quote text extraction, leading-mention removal, bot-echo filtering, and
  malformed/media-only drops.
- Protect passive-first and proactive-without-`req_id` send selection and 4,000-character
  proactive chunking using a mocked external transport seam.

## Unit 2 — Implement the authenticated WebSocket gateway

**Files:**

- Create `backend/cubeplex/im/wecom/gateway.py` for protocol constants, credential probing,
  connection lifecycle, request/ACK correlation, and inbound dispatch.
- Create `backend/tests/unit/im/wecom/test_gateway.py` for the socket protocol and lifecycle
  contracts.
- Modify `backend/pyproject.toml` and `backend/uv.lock` through `uv add aiohttp` so the WebSocket
  transport has a direct dependency declaration.

**Interfaces:**

- `probe_wecom_credentials(bot_id: str, secret: str) -> None` opens a temporary connection,
  completes `aibot_subscribe`, closes it, and raises `ValueError` with a non-secret-bearing
  message on authentication or timeout failure.
- `WecomGateway.start() -> None`, `stop() -> None`, and `is_open() -> bool` match the gateway
  lifecycle consumed by `im/runtime.py`.
- `send_passive(req_id: str, body: dict[str, Any], *, final: bool,
  skip_if_pending: bool) -> dict[str, Any]` serializes reply frames per inbound request ID.
- `send_proactive(chat_id: str, body: dict[str, Any]) -> dict[str, Any]` sends a correlated
  `aibot_send_msg` request and awaits its response.
- The gateway constructor receives the account, credentials, `ingest_inbound_event`, session
  maker, run manager, and Redis key prefix; it does not read global environment variables.

**Core logic:**

- Subscribe to the fixed `wss://openws.work.weixin.qq.com` endpoint with Bot ID, Secret, and a
  random device ID; start the read loop only after a successful matching acknowledgement.
- Register response futures before sending. The socket reader resolves matching request or
  reply-ACK waiters itself, but dispatches each callback handler in a tracked background task so
  a handler can await a passive-reply ACK without blocking the only reader that can resolve it.
- Permit one unacknowledged intermediate frame per passive request. Skip newer cumulative
  intermediate frames while it is pending; before a final frame, drain or time out the pending
  frame. If that drain times out, poison the passive request ID and switch directly to the
  proactive-final path; never register a final waiter under a key that can still receive the
  delayed intermediate ACK. Otherwise await the final frame's own ACK for 15 seconds.
- Treat a final ACK timeout after a successful socket write as delivered to avoid duplicate
  fallback messages. Surface WeCom `846604`/`846608` as expired-stream results and accept final
  `6000` as already delivered.
- Send application pings every 30 seconds. On transport loss, fail waiters, close resources,
  and reconnect with bounded backoff. Stop reconnecting after
  `aibot_event_callback/disconnected_event` to avoid mutual kicking. That terminal event invokes
  a runtime callback that removes local queue ownership, clears the shared connection heartbeat,
  writes a shared suspension marker, and releases the lease; sweeps skip the account until an
  explicit disable/re-enable clears the marker.
- Gateway stop cancels heartbeat/read/reconnect work and every tracked inbound handler, gathers
  them, fails pending futures, and closes the socket and aiohttp session without waiting for the
  next backoff interval.

**Tests:**

- Protect the exact subscribe, ping, passive-reply, and proactive-send frame shapes using fake
  socket/session objects at the outer WeCom boundary.
- Protect waiter-before-write ordering with an ACK delivered during the fake write.
- Protect intermediate-frame skip, final-frame drain, expired error mapping, benign `6000`,
  late final-ACK no-resend behavior, and proactive finalization after an intermediate timeout
  without reusing the passive request ID.
- Protect reconnect after ordinary closure, no reconnect after `disconnected_event`, and prompt
  cancellation on stop.
- Protect terminal disconnect clearing owner/heartbeat state and suppressing reacquisition until
  an explicit account toggle.
- Protect a command handler's passive-reply ACK through the real read loop, proving the reader
  remains live while the handler awaits it; also protect tracked-handler cancellation on stop.
- Protect credential-probe cleanup on success, authentication failure, and timeout.

## Unit 3 — Route inbound messages, identity commands, and conversation resets

**Files:**

- Extend `backend/cubeplex/im/wecom/gateway.py` with the account-bound inbound handler.
- Extend `backend/cubeplex/im/inbound.py` with a receipt-backed command transaction that can
  optionally require a current identity link and workspace membership before applying a
  database mutation.
- Modify `backend/cubeplex/models/im_connector.py` and add an autogenerated Alembic migration for
  a nullable generic command-response payload on `IMWebhookReceipt`; reuse the existing
  `lease_expires_at` as its delivery claim lease. Generate it from `backend/` with
  `uv run alembic revision --autogenerate -m "add im command response payload"`; do not hand-edit
  the generated operations.
- Extend `backend/cubeplex/repositories/im_connector.py` with claim, release, and
  mark-delivered operations for durable command responses.
- Create `backend/tests/e2e/test_im_wecom_ingress.py` for the real database/queue identity and
  routing flow, mocking only the WeCom send boundary.
- Reuse `backend/cubeplex/im/link.py` and the session-level reset operation behind
  `backend/cubeplex/im/reset_command.py`.

**Interfaces:**

- Inbound callbacks call `lookup_binding_mode(session_maker, account.id, channel_id)` before
  parsing, then call `ingest_inbound_event(..., identity_resolver=NullIdentityResolver(),
  rejection_notifier=bound_connector)`.
- `/link <email>` signs the existing `IMLinkClaims` with `platform="wecom"` and sends the
  confirmation URL using the current callback `req_id`.
- The command transaction inserts the durable receipt and response payload before returning any
  effect. `/link` is admitted without identity; `/new`, `/reset`, and `新对话` resolve the sender's
  current identity link and workspace membership, then reset the normalized channel and scope
  in the same transaction.
- Before sending, a callback claims the receipt response with a finite `lease_expires_at`. A
  successful gateway delivery result, including the existing post-write ACK-timeout policy,
  marks the receipt completed; send failure clears the claim, and a process crash leaves it
  retryable after expiry. Duplicate callbacks may claim and send the saved response but never
  repeat the command mutation.

**Core logic:**

- Intercept commands before ordinary run ingestion, but let only `/link` bypass identity. Reset
  commands from unlinked or removed users receive the same rejection as ordinary messages and
  cannot mutate a shared channel conversation.
- Reserve the `(account_id, msgid)` receipt before command handling. A replay observes the
  existing receipt and may retry only an undelivered saved response; it does not sign a second
  durable link response or rotate a second conversation. The response lease prevents concurrent
  deliveries from both sending it.
- Normal messages pass through the existing receipt transaction and membership gate. An
  unlinked user receives the standard link instruction; a linked user produces one queue row;
  duplicate `msgid` delivery produces no second row.
- Reload routing settings on every callback so isolated/shared changes take effect without a
  gateway reconnect.

**Tests:**

- Protect that an unlinked user is rejected with a passive `/link` instruction and no run queue
  row.
- Protect that a confirmed link allows the next callback to create one queue row whose
  `reply_to_id` is the WebSocket request ID.
- Protect duplicate-receipt idempotency and membership revocation for ordinary messages.
- Protect isolated versus shared group conversation resolution through the real Postgres
  models.
- Protect `/link` token claims and `/new` binding rotation without starting an agent run.
- Protect duplicate `/link` and reset callbacks producing one delivered reply and one reset, and
  protect unlinked and removed members from resetting both isolated and shared group scopes.
- Protect failure after command commit but before ACK: a redelivery reclaims and sends the saved
  response without repeating the reset; protect concurrent and expired response claims.

## Unit 4 — Stream run output through WeCom

**Files:**

- Create `backend/cubeplex/im/wecom/renderer.py` with `WecomOpDispatcher`.
- Create `backend/cubeplex/im/wecom/_platform.py` with the four-method `PlatformConnector`
  implementation.
- Modify `backend/cubeplex/im/runtime.py` to import/register WeCom, track live lease ownership,
  and reconcile local connections with enabled database accounts.
- Modify `backend/cubeplex/im/worker.py` and
  `backend/cubeplex/repositories/im_connector.py` so connection-based queue work is claimable
  only by the instance that owns the account's live connection.
- Modify `backend/cubeplex/api/routes/v1/_im_runtime.py`,
  `backend/cubeplex/services/im_connector.py`, and the workspace/admin account-list callers to
  batch-read shared connection heartbeats instead of relying only on process-local maps.
- Create `backend/tests/unit/im/wecom/test_renderer.py` and extend
  `backend/tests/unit/im/test_registry.py` for dispatcher and registration contracts.
- Extend `backend/tests/e2e/test_im_worker.py` and runtime lease tests for multi-instance queue
  affinity and connection reconciliation.

**Interfaces:**

- `WecomPlatform.build_tailer(...)` constructs a gateway-bound `WecomConnector`, shared
  `RenderState`, `WecomOpDispatcher`, `IMArtifactDispatcher(supports_inline_image=False)`, and
  `OutboundRunTailer`.
- `WecomOpDispatcher` implements `dispatch_create`, `dispatch_stream`, `dispatch_patch`,
  `dispatch_finalize`, `emergency_text`, and `aclose` from the existing `OpDispatcher`
  contract.
- `WecomPlatform.on_account_enabled(...)` starts one `WecomGateway` and stores it in the shared
  `gateways` map; `on_account_disabled(...)` removes and stops it.
- `IMRunQueueWorker` receives a callable snapshot of locally owned connection account IDs.
  `claim_pending_queue_item` joins the account row and permits enabled `long_connection`,
  `gateway`, or `stream` work only when its account ID is in that snapshot. Webhook accounts and
  disabled rows remain claimable by any worker, with disabled rows taking the existing terminal
  park path.
- The worker also receives an async lease validator. After all attachment preparation and
  immediately before `start_run`, it atomically compares the Redis owner value with this
  instance and renews it. Failed validation rewinds the claimed item without charging an
  attempt; the in-memory snapshot is never sufficient authorization to start a run.
- The lease owner publishes a short-lived Redis connection heartbeat only while its transport
  reports open. Account-list builders batch-read those keys and pass the shared state into
  `compute_runtime`, so owner and non-owner replicas return the same status.

**Core logic:**

- For inbound runs with a `reply_to_id`, create one stream ID, open the typing bubble, send full
  cumulative text on intermediate updates, and finish the same stream once.
- Build the visible message from pre/post-HITL text, error text, citations already folded into
  content, artifact share links, and any pending-input web-client notice.
- Cap stream content at 20,480 UTF-8 bytes and leave room for a visible truncation marker.
- If the stream is at least 330 seconds old or the server returns `846604`/`846608`, send the
  full final result proactively. If no inbound request ID exists (scheduled/triggered run),
  suppress intermediate sends and proactively send only the final result.
- Do not start native file upload. The artifact dispatcher mints share URLs and the renderer
  includes them in the final text.
- Add an explicit runtime owner set only after lease acquisition and successful connection
  startup. Release the lease when startup fails. On each sweep, stop and remove any local
  connection whose account is missing, disabled, or no longer leased by this instance, then
  renew or acquire enabled orphan accounts. The WeCom callback handler separately reloads the
  account's enabled state before commands or normal ingestion, closing the sweep race window.
- Use compare-and-expire Lua for lease validation/renewal so an expired old owner cannot extend a
  lease already acquired by another instance. Refresh the shared connection heartbeat only
  after that atomic renewal and only while `is_open()` is true.
- A terminal-disconnect callback removes the account from the owner set, deletes its heartbeat,
  sets a shared suspension marker, and releases the lease. All sweep instances check that marker
  before acquisition. Disable/re-enable clears it as an operator-authorized retry.

**Tests:**

- Protect one stable stream ID across create, cumulative updates, and finalize.
- Protect UTF-8 byte-safe truncation and its marker.
- Protect expired-stream and no-`req_id` proactive-final behavior.
- Protect final render inclusion of errors, artifact links, and pending-input web notices.
- Protect gateway start/stop wiring through the registry and shared runtime maps.
- Protect that a non-owner worker cannot claim or start a run for a connection account, while
  the owner can and webhook work remains instance-agnostic.
- Protect a stale snapshot whose live Redis lease belongs to another instance: the immediate
  pre-start validation rewinds the item and `start_run` is never called.
- Protect owner reconciliation after disable, delete, and lease loss, including local gateway
  shutdown, owner-set removal, and lease release; protect the ingress enabled-row guard during
  the reconciliation interval.
- Protect shared heartbeat publication/removal and an account-list request served by a non-owner
  replica reporting the owner's live WeCom connection state.

## Unit 5 — Add the workspace account contract

**Files:**

- Modify `backend/cubeplex/api/schemas/im_connector.py` to add
  `ConnectWecomAccountIn` to the discriminated union.
- Modify `backend/cubeplex/services/im_connector.py` to add `connect_wecom` with duplicate
  preflight, credential probe, encrypted storage, and orphan cleanup.
- Modify `backend/cubeplex/api/routes/v1/ws_im.py` to dispatch WeCom account creation and start
  its gateway after commit.
- Extend `backend/tests/unit/test_im_schemas.py` and
  `backend/tests/e2e/test_im_routes.py` for the public API contract.

**Interfaces:**

- `ConnectWecomAccountIn` has `platform: Literal["wecom"]`, required `bot_id`, required
  `secret`, and `acting_user_id="self"`.
- `IMConnectorService.connect_wecom(*, workspace_id, bot_id, secret,
  acting_user_id) -> IMConnectorAccount` stores a `stream` account with `bot_id` as its external
  and bot-open identifiers.
- The existing workspace `POST /api/v1/ws/{workspace_id}/im/accounts` returns the unchanged
  `IMAccountOut` shape.

**Core logic:**

- Duplicate preflight happens before credential validation and creation. Credential probe
  happens before durable writes. Credential and account creation preserve the existing
  best-effort atomic cleanup used by the other platforms.
- `ValueError` from authentication maps to 400; duplicate account/race maps to 409. Neither
  response includes the submitted Secret.
- Workspace scoping and acting-user impersonation continue through the existing route helper;
  admin list/toggle routes remain separate and unchanged.

**Tests:**

- Protect Pydantic discrimination and required fields.
- Protect connect/list/delete through the real FastAPI app, Postgres, and credential vault while
  mocking only `probe_wecom_credentials`.
- Protect invalid credentials creating neither account nor credential, duplicate creation
  returning 409, and list/admin responses not leaking the Secret.
- Protect immediate gateway-start invocation after a successful workspace bind.

## Unit 6 — Add the connection wizard and client types

**Files:**

- Modify `frontend/packages/core/src/api/im.ts` and
  `frontend/packages/core/src/api/__tests__/im.test.ts` for the WeCom request type.
- Create `frontend/packages/web/components/im/ImConnectWizard/platforms/wecom.ts`.
- Modify `frontend/packages/web/components/im/ImConnectWizard/platforms/types.ts` and
  `platforms/index.ts` to expose the live platform.
- Modify `frontend/packages/web/components/im/PlatformLogo.tsx` for the WeCom mark.
- Modify `frontend/packages/web/components/im/ImConnectWizard/useConnectMutation.ts` and its
  nearest unit test to associate WeCom credential errors with `secret`.
- Modify `frontend/packages/web/messages/en.json` and `messages/zh.json` for platform, wizard,
  prerequisite, field, and error copy.

**Interfaces:**

- `ConnectWecomAccountIn` mirrors the backend payload and joins `ConnectImAccountIn`.
- The descriptor ID union gains `wecom`; `buildPayload` returns `{platform: "wecom", bot_id,
  secret, acting_user_id: "self"}`.
- `classifyConnectError` receives the attempted platform so HTTP 400 can select `secret` for
  WeCom without changing the existing field mapping for other connectors.

**Core logic:**

- Reuse the current prerequisites → credentials → verify wizard and existing tokenized UI
  primitives. The console link points to WeCom administration; there is no QR flow or delivery
  mode selector.
- Show Bot ID as text and Secret as a password input. Do not add a page, token, dependency, or
  WeCom-specific form component.

**Tests:**

- Protect the exact core API payload and path.
- Protect that WeCom HTTP 400 maps to the `secret` field while duplicate and network failures
  keep the shared banner/toast behavior.
- Skip DOM-presence tests: the descriptor and shared wizard do not create a new client state
  machine.

## Unit 7 — Ship product documentation

**Files:**

- Create `docs/site/docs/guides/im/wecom.md` and
  `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/guides/im/wecom.md`.
- Modify both English and Simplified Chinese `guides/im/overview.md` files.
- Add explicit screenshot placeholders under the sanctioned WeCom guide paths if real console
  captures are not available.

**Interfaces:**

- The platform table lists WeCom as a Stream/WebSocket connector and links to the new guide.
- The guide uses the shipped API/UI field names: Bot ID and Secret.

**Core logic:**

- Document AI Bot creation, outbound-only WebSocket requirements, workspace binding, identity
  linking, direct/group use, conversation commands, long-connection re-enable behavior, and
  credential rotation.
- State the v1 limits plainly: no personal WeChat, callback app, native media, QR setup, or
  in-chat interactive input.

**Tests:**

- Run the docs build so broken links, front matter, or malformed MDX fail before push.

## Verification and delivery

Run changed-module tests after each red/green/refactor unit, capturing noisy output under
`tmp/wecom-*.log`. Before the code push:

1. Run the focused backend unit tests for `im/wecom`, schemas, registry, and runtime behavior.
2. Run the focused backend e2e IM route and WeCom ingress tests against the worktree test DB.
3. Build `@cubeplex/core`, run the focused frontend unit tests, and run frontend typecheck/lint
   for the touched packages.
4. Build the documentation site.
5. Run one manual protocol smoke test only when Bot ID and Secret are available; otherwise
   record that the authenticated external-WeCom boundary remains operator verification.
6. Push code normally so the pre-push hook runs the relevant `make check-ci` gates; do not run
   those full gates separately first.

The implementation remains on `feat/2026-09-04-wecom-im`. The existing spec/plan PR receives
the code as follow-up commits, and its title remains a brief description without a static
prefix.
