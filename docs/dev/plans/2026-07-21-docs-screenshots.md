# Documentation screenshot plan

## Goal

Replace every remaining screenshot placeholder in the English and Simplified
Chinese site docs with a real, readable screenshot. Each English/Chinese pair
uses one shared asset under `docs/site/static/img/`. The latest main baseline
had 53 remaining assets (106 locale-specific positions). After the current
capture pass, 40 placeholder blocks remain in each locale (80 locale-specific
positions); every remaining block is listed as an explicit blocker below.

## Current baseline

- Worktree: `feat/2026-07-18-docs-screenshots`
- Base: latest `origin/main` at `660ac4f2`
- Browser/app environment: use the worktree's `.worktree.env` values, not the
  main-worktree defaults.
- Already captured and re-applied: `first-conversation.png`,
  `new-task-dialog.png`, `workspace-mcp-page.png`, `workspace-skills-page.png`,
  the admin/settings/automation assets listed in the notes file, and their
  English/Chinese references.

## Remaining capture groups (53 assets)

### CubePlex product UI (28 assets)

Capture the real page with seeded, non-sensitive demo data. If a page has a
detail panel, select a record before capturing; an empty right panel is not an
acceptable result.

- Admin: `admin/cost-dashboard.png`, `mcp/admin-catalog.png`,
  `mcp/admin-distribute-dialog.png`, `mcp/admin-workspaces-tab.png`,
  `admin/members-table.png`, `admin/models-providers.png`,
  `admin/sandbox-network-policy.png`, `admin/add-skill-registry.png`,
  `admin/model-providers.png`.
- Settings and automation: `settings/avatar-editor.png`,
  `automation/trigger-webhook-url-secret.png`,
  `automation/trigger-event-log.png`, `scheduled-tasks/run-history.png`.
- Conversations and topics: `conversations/artifact-panel.png`,
  `conversations/conversation-layout.png`,
  `conversations/sandboxes-panel.png`,
  `conversations/topic-create-dialog.png`,
  `conversations/topic-members-panel.png`.
- Workspace integrations and catalogs:
  `im/connectors-list.png`, `im/dingtalk-cubeplex-connect-form.png`,
  `im/discord-cubeplex-connect-form.png`, `im/feishu/cubeplex-connect-form.png`,
  `im/slack-cubeplex-connect-form.png`, `im/teams-cubeplex-connect-form.png`,
  `mcp/workspace-catalog.png`, `mcp/oauth-connect.png`,
  `memory/memory-center.png`, `skills/marketplace.png`.

### Third-party platform consoles (25 assets)

Use the user's existing signed-in browser sessions when available. Redact
tokens, secrets, IDs, email addresses, and tenant-specific URLs before saving.
If a platform account or app is unavailable, record that asset as blocked in
the notes instead of fabricating a console screenshot.

- DingTalk: `im/dingtalk-app-credentials.png`,
  `im/dingtalk-bot-capability.png`, `im/dingtalk-stream-mode.png`,
  `im/dingtalk-permissions.png`.
- Discord: `im/discord-create-application.png`, `im/discord-bot-token.png`,
  `im/discord-message-content-intent.png`, `im/discord-oauth-invite.png`.
- Feishu/Lark: `im/feishu/console-app-credentials.png`,
  `im/feishu/console-bot-capability.png`,
  `im/feishu/console-permissions.png`,
  `im/feishu/console-event-delivery.png`,
  `im/feishu/console-token-encrypt.png`,
  `im/feishu/console-subscribe-message.png`.
- Slack: `im/slack-create-app.png`, `im/slack-socket-mode.png`,
  `im/slack-app-token.png`, `im/slack-bot-scopes.png`,
  `im/slack-event-subscriptions.png`, `im/slack-install-token.png`.
- Microsoft Teams/Azure: `im/teams-azure-bot-create.png`,
  `im/teams-app-secret.png`, `im/teams-messaging-endpoint.png`,
  `im/teams-channel-enable.png`, `im/teams-manifest.png`.

## Current blockers

- Third-party DingTalk, Discord, Feishu, Slack, and Teams/Azure console
  screenshots require external console access or permissions that are not
  available in the current browser session.
- Conversation artifact and basic-layout screenshots are complete: a real agent
  run produced a populated artifact preview panel. Conversation sandbox/topic
  captures remain blocked because no non-empty, non-sensitive demo state is
  available for those panels.
- Admin cost, admin MCP distribution, OAuth connect, and the external skill
  marketplace currently load with no usable non-sensitive data or registry
  results, so those assets remain placeholders until a seeded demo path or
  registry session is available.

## Execution and acceptance

1. Start backend and frontend with the worktree ports from `.worktree.env`.
2. Register a disposable demo account if the worktree has no usable session.
3. Seed only reversible, non-sensitive records needed to make list and detail
   pages informative. Do not create recurring tasks or external connections
   that can incur real model/provider charges without confirmation.
4. Capture one asset at a time, then inspect it visually for real content,
   selected detail state, dark English UI, and absence of secrets.
5. Replace both locale placeholders for every accepted asset.
6. Verify image MIME/format, dimensions, document references, and remaining
   placeholder counts. Record blockers in the notes file.
