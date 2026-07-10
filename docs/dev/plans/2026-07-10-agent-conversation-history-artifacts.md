# Agent conversation history and artifacts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give agents read-only, workspace-scoped tools to search and read prior conversation turns, retrieve one historical tool result on demand, and list accessible artifacts.

**Architecture:** Add a pure formatter in the existing `conversation_search` package so it shares the package's checkpointer-message sequence semantics while grouping persisted cubepi messages into bounded user-initiated turns. Add a dynamic `conversation_history` capability for run-scoped search dependencies and a static `artifacts` capability backed by scoped repositories. Register both through the deferred capability registry.

**Tech Stack:** Python 3.13, Pydantic v2, cubepi `AgentTool`, SQLAlchemy async, FastAPI test client, pytest, Docusaurus Markdown.

## Global Constraints

- Every operation is read-only and available in interactive, scheduled, and IM runs.
- Every query enforces org, workspace, and current-user conversation visibility.
- `conversation_history_read` defaults to five user-initiated turns and returns selected turns chronologically.
- Normal history reads contain tool-call summaries only; detailed tool output is available only from the targeted result operation. Oversized turns may expose only a prefix of calls plus `tool_calls_omitted`; re-read with a larger budget before using targeted lookup for omitted calls.
- Artifact deletion and artifact file reads are out of scope.
- Preserve deterministic deferred/eager tool registration order for prompt caching.
- Database/app-client tests go in `backend/tests/e2e/`; update user-facing site docs in the same change.

---

## File structure

- Create `backend/cubebox/services/conversation_search/history.py`: pure history parsing, tool-call summarization, and estimated-token truncation.
- Create `backend/cubebox/agents/actions/capabilities/conversation_history.py`: input models and three read-only history operations.
- Create `backend/cubebox/agents/actions/capabilities/artifacts.py`: scoped artifact listing.
- Modify `backend/cubebox/agents/actions/registry.py`: history runtime deps and group registration.
- Modify `backend/cubebox/streams/run_manager.py`: pass embedding provider and lexical backend to history actions.
- Create `backend/tests/unit/services/conversation_search/test_history.py`: formatter contract.
- Create `backend/tests/e2e/test_agent_history_artifacts_actions.py`: DB-backed scope and handler contracts.
- Modify `docs/site/docs/guides/conversations/basics.md`: user-visible behavior.

## Task 1: Build the history formatter

**Files:**
- Create: `backend/cubebox/services/conversation_search/history.py`
- Test: `backend/tests/unit/services/conversation_search/test_history.py`

**Interfaces:**
- Produces `format_history_turns(messages: list[dict[str, Any]], *, n: int, max_tokens: int, before_seq: int | None) -> FormattedHistoryPage`.
- Produces `format_tool_result(messages: list[dict[str, Any]], *, tool_call_id: str, max_tokens: int) -> FormattedToolResult | None`.
- Both formatter functions reject `max_tokens < 256`, matching the capability input contract.

- [ ] **Step 1: Write the failing test**

```python
def test_history_page_returns_complete_recent_turns_without_result_bodies() -> None:
    page = format_history_turns(MESSAGES, n=5, max_tokens=4_000, before_seq=None)

    assert [turn["user"]["text"] for turn in page.turns] == ["older", "newer"]
    assert page.turns[-1]["tool_calls"][0]["tool_call_id"] == "call-1"
    assert "tool result body" not in str(page.turns)


def test_targeted_tool_result_obeys_its_token_budget() -> None:
    result = format_tool_result(MESSAGES, tool_call_id="call-1", max_tokens=256)

    assert result is not None
    assert result.tool_call_id == "call-1"
    assert result.truncated is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov 2>&1 | tee tmp/history-format-red.log | tail -3`

Expected: FAIL because `conversation_search.history` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class FormattedHistoryPage:
    turns: list[dict[str, Any]]
    has_more: bool
    next_before_seq: int | None
    estimated_tokens: int
    truncated: bool


def estimate_tokens(value: object) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)
```

Partition sorted persisted messages at each `role == "user"`; attach following assistant text and tool-call summaries until the next user message. Correlate `tool_result.tool_call_id` only to derive call status, never to include its body. Select newest complete turns until `n` or `max_tokens`, then reverse them for chronological output. If one turn alone exceeds budget, truncate text fields and, if necessary, the tool-call list. Keep every returned call identity/status usable for targeted lookup, record the number removed as `tool_calls_omitted`, and set `truncated=True`. Budget the complete page envelope, including `estimated_tokens`, not only its turns. Redact compact argument values for keys containing `secret`, `token`, `password`, `authorization`, or `api_key`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py --no-cov 2>&1 | tee tmp/history-format-green.log | tail -3`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/services/conversation_search/history.py backend/tests/unit/services/conversation_search/test_history.py
git commit -m "feat: format historical agent turns"
```

## Task 2: Add read-only capability handlers

**Files:**
- Create: `backend/cubebox/agents/actions/capabilities/conversation_history.py`
- Create: `backend/cubebox/agents/actions/capabilities/artifacts.py`
- Modify: `backend/cubebox/agents/actions/registry.py`
- Modify: `backend/cubebox/streams/run_manager.py`
- Test: `backend/tests/e2e/test_agent_history_artifacts_actions.py`

**Interfaces:**
- Produces `ConversationHistoryDeps(provider: EmbeddingProvider | None, lexical_backend: LexicalSearchBackend | None)`.
- Produces `build_conversation_history_capability(deps: ConversationHistoryDeps) -> AgentCapability`.
- Produces `ARTIFACTS_CAPABILITY: AgentCapability`.
- Extends `tools_for_run(..., history_deps: ConversationHistoryDeps | None = None)`.

- [ ] **Step 1: Write failing e2e tests**

```python
@pytest.mark.asyncio
async def test_history_read_rejects_a_conversation_outside_the_caller_scope(seed: Seed) -> None:
    result = await invoke_history_read(seed.stranger_context, conversation_id=seed.private_conversation_id)

    assert result.is_error is True
    assert "ActionNotFound" in result.content[0].text


@pytest.mark.asyncio
async def test_artifact_list_excludes_inaccessible_conversation_artifacts(seed: Seed) -> None:
    payload = await invoke_artifacts_list(seed.member_context, n=10)

    assert seed.visible_artifact_id in payload
    assert seed.hidden_artifact_id not in payload
```

Cover search context IDs, formatted five-turn reads without result bodies, successful targeted tool-result lookup, and Pydantic bounds for `n` and `max_tokens`.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd backend && uv run pytest tests/e2e/test_agent_history_artifacts_actions.py --no-cov 2>&1 | tee tmp/history-actions-red.log | tail -3`

Expected: FAIL because capability modules and registry dependencies do not exist.

- [ ] **Step 3: Implement conversation history**

```python
class ReadInput(BaseModel):
    conversation_id: str
    n: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=4_000, ge=256, le=12_000)
    before_seq: int | None = Field(default=None, ge=1)


async def _visible_conversation(
    ctx: ScopeContext, session: AsyncSession, conversation_id: str
) -> Conversation:
    repo = ConversationRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id, user_id=ctx.user_id
    )
    conversation = await repo.get_by_id(conversation_id)
    if conversation is None:
        raise ActionNotFound("conversation not found")
    return conversation
```

Use `_visible_conversation` before every checkpoint read. Search with `ConversationSearchService(session, deps.provider, lexical_backend=deps.lexical_backend)` and `ctx.org_id/workspace_id/user_id`. Add `conversation_history_search`, `conversation_history_read`, and `conversation_history_tool_result` as `mutates=False` operations.

- [ ] **Step 4: Implement artifacts and register both groups**

```python
class ListInput(BaseModel):
    n: int = Field(default=10, ge=1, le=50)
    q: str | None = Field(default=None, max_length=255)
    artifact_type: str | None = Field(default=None, max_length=50)
    offset: int = Field(default=0, ge=0)
```

Build the accessible-conversation subquery from `ConversationRepository` and pass it to `ArtifactRepository.list_by_workspace`; return `Artifact.to_dict()` values only. Add `ARTIFACTS_CAPABILITY` to static capabilities. In `run_manager.py`, capture `self._app.state.embedding_provider` and `lexical_backend`, create `ConversationHistoryDeps`, and pass it to `tools_for_run`. Register history only when its deps are available; do not change eager tool order.

- [ ] **Step 5: Run e2e tests to verify GREEN**

Run: `cd backend && uv run pytest tests/e2e/test_agent_history_artifacts_actions.py --no-cov 2>&1 | tee tmp/history-actions-green.log | tail -3`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/agents/actions/capabilities backend/cubebox/agents/actions/registry.py backend/cubebox/streams/run_manager.py backend/tests/e2e/test_agent_history_artifacts_actions.py
git commit -m "feat: expose history and artifacts to agents"
```

## Task 3: Document and verify

**Files:**
- Modify: `docs/site/docs/guides/conversations/basics.md`
- Test: `backend/tests/unit/services/conversation_search/test_history.py`
- Test: `backend/tests/e2e/test_agent_history_artifacts_actions.py`

**Interfaces:**
- Documents access to prior work without making implementation-specific tool names part of the user contract.

- [ ] **Step 1: Update the conversations guide**

Add a “Using prior work” subsection: the agent can search current-workspace conversations and artifacts visible to the signed-in user; it reads a small recent-turn window by default; detailed historical tool output is fetched only when needed; it cannot delete artifacts through this capability.

- [ ] **Step 2: Run focused verification**

Run: `cd backend && uv run pytest tests/unit/services/conversation_search/test_history.py tests/e2e/test_agent_history_artifacts_actions.py --no-cov 2>&1 | tee tmp/history-artifacts-verify.log | tail -3`

Expected: PASS.

- [ ] **Step 3: Run static checks**

Run: `cd backend && uv run ruff check cubebox/agents/actions && uv run ruff format --check cubebox/agents/actions && uv run mypy cubebox/agents/actions 2>&1 | tee tmp/history-artifacts-static.log | tail -5`

Expected: all commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/site/docs/guides/conversations/basics.md
git commit -m "docs: describe agent access to prior work"
```

## Plan self-review

- Spec coverage: Task 1 delivers formatted token-bounded turns and targeted result lookup; Task 2 delivers both deferred groups, runtime search dependencies, and scope enforcement; Task 3 documents behavior and verifies all targeted contracts.
- Placeholder scan: no deferred implementation placeholders are present.
- Type consistency: `ConversationHistoryDeps` is created in `run_manager.py`, consumed by `tools_for_run`, and passed to `build_conversation_history_capability`; artifacts is static and requires no runtime dependency.
