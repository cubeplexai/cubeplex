# Minimal skill example (fictional)

Copy and adapt. This is not a real installed skill name.

## Layout

```text
/workspace/skills/weekly-status-summary/
  SKILL.md
```

## SKILL.md

```markdown
---
name: weekly-status-summary
description: >
  Drafts a short weekly status summary from the user's notes or bullet list.
  Use when the user asks for a weekly status, progress update, or standup
  write-up from notes they provide.
keywords:
  - weekly-status
  - standup
  - summary
---

# Weekly status summary

## Steps

1. Collect the user's raw notes for the week (bullet list is fine). If anything
   is missing (audience, date range), ask one short clarifying question.
2. Write a concise status with three sections: **Done**, **In progress**,
   **Risks / blockers**. Use only facts from the notes — do not invent metrics.
3. Save the result as a markdown file under `/workspace/weekly-status/` and
   register it with `save_artifact` so the user can download it. Prefer the
   artifact over dumping a long report only in chat.
4. Reply with a 2–3 sentence overview and point at the artifact.
```

Notes for authors:

- Description is third person and states WHEN it should load.
- Body is second person and names only the tool this workflow needs
  (`save_artifact`) — no full tool catalog.
- No `version` field: the server assigns the next patch on publish.
