# Documentation screenshot work notes

## 2026-07-21

- Updated `feat/2026-07-18-docs-screenshots` from `09fa71e5` to the latest
  `origin/main` at `660ac4f2` with a fast-forward merge.
- Worktree environment is slot 19: backend `8019`, frontend `3019`, database
  `cubeplex_feat_2026_07_18_docs_screenshots`.
- Preserved the four previously validated screenshots and their English and
  Simplified Chinese references while updating the branch baseline.
- The browser automation bridge is slow on this app. Direct navigation can
  time out while still completing; check the final URL before retrying.
- Coordinate clicks are unreliable when catalog data reorders during load.
  For detail pages, use a unique visible-text locator, verify its count is 1,
  and only then click.
- A useful screenshot must show substantive data and a selected detail panel.
  Empty list states and empty right panels are rejected.
- Do not publish screenshots containing personal email, API-key prefixes,
  opaque secret placeholders, internal registry/image names, or private memory
  content. Recreate a sanitized demo state or mark the asset blocked.
- The worktree demo state now includes reversible, labeled records for members,
  sandbox policy rules, a trigger and its event log, five memory cards, and a
  scheduled task. Trigger events and scheduled-task runs were inserted as
  explicit demo rows without invoking an LLM, so the screenshots show useful
  failure/success states without creating provider charges.
- Captured `scheduled-tasks/run-history.png` after refreshing the task detail
  panel. It shows the daily schedule, next run, Pause control, and Succeeded
  plus Skipped (missed) history rows. The asset is referenced by both English
  and `zh-Hans` scheduled-task docs.
- Chrome's screenshot method returns JPEG bytes even when the requested name
  ends in `.png`. Every accepted capture is converted with `sips` and checked
  with `file`; the refreshed captures are real 1712×942 PNG files.
- Current accepted product captures include the admin model/provider, members,
  sandbox, skill registry, profile/avatar, trigger detail/event log, scheduled
  task run history, workspace MCP catalog, memory center, workspace skills,
  first conversation, and new task dialog assets. Each accepted asset is wired
  into both locale trees.
- A real agent run now produces a substantive artifact card and rendered
  right-side preview. `conversations/artifact-panel.png` and
  `conversations/conversation-layout.png` are real dark English-interface
  captures wired into both locale trees. Remaining placeholder inventory is 40
  blocks in English and 40 matching blocks in `zh-Hans` (12 files per locale):
  cost tracking, admin MCP catalog/distribution, conversation sandbox/topics,
  OAuth connect, and the skills marketplace. Third-party IM console captures
  remain blocked by external-console login/permission state; no empty panel was
  promoted to an accepted asset.
- The Next.js development route/build indicator was disabled in
  `frontend/packages/web/next.config.ts`; a browser DOM check confirmed the
  indicator is absent, and the web package type-check passed.
- Remaining work is tracked in
  `docs/dev/plans/2026-07-21-docs-screenshots.md`.

## 2026-07-21 follow-up: formal demo identity and runtime recovery

- The backend process was still listening on `8019`, but its SSH forwards to
  the remote test services had stopped. Restarted the forwards for Postgres,
  Redis, Tempo, the sandbox gateway, and WebTools, then restarted the backend.
- The first backend restart failed because the inherited `.worktree.env`
  database name (`cubeplex_feat_2026_07_18_docs_screenshots`) overrode the
  remote configuration. The working runtime command explicitly sets
  `CUBEPLEX_DATABASE__NAME=cubebox` while retaining the worktree API port.
- Updated the signed-in demo account through Profile: display name `Chris`.
  The profile email field is intentionally read-only, so the remote test row
  was changed from `codex-docs-screenshots-20260721@example.com` to
  `chris@cubeplex.ai`. The seeded member was similarly normalized to
  `Docs Reviewer <docs-reviewer@cubeplex.ai>`.
- Re-captured every accepted product asset that can be produced in the
  current test environment, so the lower-left identity and member tables no
  longer show the task-generated `codex-docs-screenshots` or `example.com`
  values. The MCP detail capture now uses the enabled, no-credential-required
  Microsoft Learn connector and has a populated right-side detail panel.
- The build-based docs server remains available at `http://localhost:3040`;
  the Chinese entry is `/docs/zh-Hans` (without a trailing slash).
