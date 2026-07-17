export const meta = {
  name: 'docs-module-review',
  description: 'Per-module review + in-place correction of the CubePlex docs site against the code',
  phases: [
    { title: 'Review & correct' },
    { title: 'Synthesize' },
  ],
}

// All agents operate inside this worktree. Absolute paths only — subagents do
// not inherit cwd.
const WT = '/home/chris/cubeplex/.worktrees/feat/2026-06-23-docs-overhaul'
const DOCS = `${WT}/docs/site/docs`
const CODE = `${WT}/backend/cubeplex`
const FE = `${WT}/frontend/packages/web`

const PLACEHOLDER = [
  'Screenshot placeholder convention — insert where a visual is genuinely needed',
  '(key UI surface that prose cannot fully convey: panels, pickers, dashboards,',
  'state transitions). Do NOT spam them. Use this exact Docusaurus admonition:',
  '',
  ':::info 📸 Screenshot placeholder',
  '**Capture:** <what to show, including the interaction/state to demonstrate>',
  '**Asset:** `/img/<area>/<name>.png`',
  ':::',
].join('\n')

const RULES = `
You are correcting ONE module of the CubePlex user-facing docs site (Docusaurus).
Worktree root: ${WT}
Docs root:     ${DOCS}
Backend code:  ${CODE}
Frontend code: ${FE}

Your job, in order:
1. READ every assigned doc file in full.
2. VERIFY each concrete claim against the code by grepping/reading the mapped
   source dirs: routes & path prefixes, HTTP methods, header names, enum values,
   default limits, tool names, state-machine states, role/permission tables,
   config keys. Treat the code as ground truth.
3. CORRECT the docs IN PLACE with the Edit/Write tools. Fix factual drift, wrong
   routes, wrong enums, wrong limits. Preserve the existing voice, structure,
   and the "Tips" sections — surgical edits, not rewrites, unless a section is
   wholesale wrong.
4. NORMALIZE typography: ' -- ' used as an em-dash must become ' — ' (an actual
   em-dash). Do NOT touch '--' inside code blocks, CLI flags, or inline code.
5. DE-LEAK internal paths: Next.js route groups like '/(app)/...' and pseudo
   patterns like '/<workspace>/...' must never appear in user docs. Replace with
   the real user-facing URL (verify it) or describe the navigation instead.
6. SCREENSHOTS: ${PLACEHOLDER}
7. HONESTY: if you cannot verify a claim in the code, DO NOT invent a fact and
   DO NOT delete the claim silently — leave it, and record it under
   residual_gaps with the exact file:line and what you'd need to confirm it.

Only edit the files assigned to you. Do not run servers, migrations, or tests.
After editing, return ONLY the structured report object.
`

const REPORT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['module', 'files_touched', 'corrections', 'placeholders_added', 'residual_gaps'],
  properties: {
    module: { type: 'string' },
    files_touched: { type: 'array', items: { type: 'string' } },
    corrections: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['file', 'was_wrong', 'fix', 'evidence'],
        properties: {
          file: { type: 'string' },
          was_wrong: { type: 'string' },
          fix: { type: 'string' },
          evidence: { type: 'string', description: 'code path:line proving the fix' },
        },
      },
    },
    placeholders_added: { type: 'integer' },
    residual_gaps: { type: 'array', items: { type: 'string' } },
  },
}

const MODULES = [
  {
    key: 'getting-started',
    prompt: `Module: Getting Started.
Files: ${DOCS}/intro.mdx, ${DOCS}/getting-started/quick-start.md,
${DOCS}/getting-started/core-concepts.md, ${DOCS}/getting-started/workspace-setup.md
Verify against: ${CODE}/auth, ${CODE}/api/routes/v1 (registration, workspaces, members),
${CODE}/models, and ${WT}/backend/docs/auth.md.
Focus: org/workspace role tables and capabilities; single_tenant vs multi_tenant
bootstrap (first-user-becomes-owner); the real post-register landing URL; and
NAVIGATION NOMENCLATURE — the docs mix "Settings > Models", "Organization
Settings > Models", and "Admin > Models" for the same destination. Pick the one
that matches the actual UI/route and make it consistent across THIS module
(note the chosen vocabulary in residual_gaps so other modules can align).`,
  },
  {
    key: 'conversations',
    prompt: `Module: Conversations.
Files: ${DOCS}/guides/conversations/basics.md, .../attachments.md,
.../artifacts.md, .../model-selection.md
Verify against: ${CODE}/agents, ${CODE}/services/attachments.py,
${CODE}/api/routes/v1/conversations.py, ${FE}/components/chat.
Focus: thinking levels (off/low/medium/high/xhigh — confirm the displayed label
for 'xhigh'); attachment limits (max_file_bytes, max_per_conversation_bytes, AND
the per-message cap attachments.max_per_message — currently UNDOCUMENTED, add
it); artifact types and the save_artifact/generate_image tools; the failover
banner text and whether hard-coded dated model IDs should be replaced with
generic placeholders; preset stickiness behavior.`,
  },
  {
    key: 'skills',
    prompt: `Module: Skills.
Files: ${DOCS}/guides/skills/overview.md, .../discover-and-install.md,
.../managing-skills.md, ${DOCS}/admin/skills-management.md
Verify against: ${CODE}/skills, ${FE} skills pages.
Focus: skill states/source enums; the in-sandbox mount path
('/.skills/<name>/<version>/' — confirm exact); load_skill tool; remote registry
flow and trust tiers; org-wide vs workspace-private install semantics; and the
'/<workspace>/skills' pseudo-URL leak in discover-and-install.md.`,
  },
  {
    key: 'memory',
    prompt: `Module: Memory.
Files: ${DOCS}/guides/memory/overview.md, .../using-memory.md, .../managing-memory.md
Verify against: ${CODE}/memory.
Focus: the three tiers and precedence; the six memory types (confirm the exact
enum strings); confidence range; Active/Archived status; the
'/(app)/w/{workspaceId}/memory' route-group LEAK in managing-memory.md (replace
with the real user URL); and the who-can-view/edit permission table.`,
  },
  {
    key: 'mcp',
    prompt: `Module: MCP Tools.
Files: ${DOCS}/guides/mcp/overview.md, .../installing-connectors.md,
.../using-tools.md, ${DOCS}/admin/mcp-connectors.md
Verify against: ${CODE}/mcp and ${WT}/backend/docs/mcp_catalog_oauth.md.
Focus: the Template→Install→Grant→Active lifecycle; the listed connector catalog
(does it match what ships?); auth modes (API key / OAuth / bearer); PKCE + DCR
claims and which providers need pre-registered OAuth apps; progressive
disclosure; tool citations; admin route paths.`,
  },
  {
    key: 'automation-schedules',
    prompt: `Module: Scheduled Tasks.
Files: ${DOCS}/guides/automation/scheduled-tasks.md
Verify against: ${CODE}/schedules (and any trigger/destination code).
Focus: schedule kinds (cron / fixed interval / one-shot); missed-run policy
options (Skip / Run latest — confirm exact set and names); conversation options
(fixed vs new-per-fire); run-history fields; first-fire timing for intervals.`,
  },
  {
    key: 'automation-triggers',
    prompt: `Module: Event Triggers — VERIFY-ONLY + typography.
File: ${DOCS}/guides/automation/event-triggers.md
This file was just hand-corrected against ${CODE}/triggers (ingest.py,
signature.py, ${CODE}/api/routes/v1/trigger_ingest.py). DO NOT re-rewrite the
webhook facts. Your ONLY edits: normalize remaining ' -- ' to ' — ' outside code
blocks. Then verify the corrected facts still match the code and report any
discrepancy you find under residual_gaps. Keep placeholders_added at the count
you actually add (likely 0).`,
  },
  {
    key: 'admin',
    prompt: `Module: Administration.
Files: ${DOCS}/admin/models.md, ${DOCS}/admin/members.md, ${DOCS}/admin/sandbox.md,
${DOCS}/admin/cost-tracking.md
Verify against: ${CODE}/api/routes/v1 (admin_*.py), ${CODE}/sandbox, ${CODE}/credentials,
and cost/usage code.
Focus: admin route paths (/admin/...); sandbox supported languages list (confirm
against OpenSandbox integration); env-var scope precedence (User>Workspace>Org);
secret handling claims; org role table and Transfer Ownership; cost-tracking
inputs (input/output tokens, per-model rates).`,
  },
  {
    key: 'im-connectors',
    isAuthoring: true,
    prompt: `Module: IM Connectors — AUTHORING (this surface has ZERO docs today).
Code: ${CODE}/im (feishu, dingtalk, slack, teams, discord), ${CODE}/api/routes/v1/im_ingress.py,
admin_im.py, and ${WT}/backend/cubeplex/im/feishu/*.
Task:
1. CREATE ${DOCS}/im/overview.md — what IM connectors are, the supported
   platforms (state honestly which are mature vs early, based on the code), the
   general model (bind a bot → inbound webhook → agent run → reply), identity
   linking, and the /new and /reset commands if present in code.
2. CREATE ${DOCS}/im/feishu.md — a concrete setup guide for Feishu/Lark (the most
   developed platform per the code): create the bot, configure the event/webhook
   URL and encrypt/verification-token, the signature scheme (verify against
   ${CODE}/im/feishu/signature.py), and how a workspace user links their account.
   Use screenshot placeholders for every external-console step.
3. Register both pages in ${WT}/docs/site/sidebars.ts under a new
   "IM Connectors" category (place it after Automation). Keep the file valid TS.
4. For dingtalk/slack/teams/discord, do NOT fabricate full guides — add a short
   "Other platforms" section in overview.md listing them with a one-line status
   and a residual_gap noting each needs its own page.
Accuracy over completeness: only state what the code supports. Everything
unconfirmed goes in residual_gaps.`,
  },
]

phase('Review & correct')
const reports = await parallel(
  MODULES.map((m) => () =>
    agent(`${RULES}\n\n${m.prompt}`, {
      label: `doc:${m.key}`,
      phase: 'Review & correct',
      agentType: 'claude',
      schema: REPORT_SCHEMA,
    }),
  ),
)

const clean = reports.filter(Boolean)

phase('Synthesize')
const summary = await agent(
  `You are the docs-overhaul synthesizer. Below are JSON change reports from
per-module doc-correction agents for the CubePlex docs site.

${JSON.stringify(clean, null, 2)}

Produce a concise Markdown report for the human reviewer with:
- A one-paragraph overview of what changed across the site.
- A table of the highest-impact factual corrections (module | was wrong | fix | evidence).
- The consolidated list of residual gaps / unverified claims, grouped by module,
  that a human must confirm or that need follow-up (call out the i18n decision,
  the remaining IM platform pages, and any nav-nomenclature conflicts between
  modules).
- A short "screenshots to capture" checklist aggregated from every placeholder
  added (asset path + what to capture).
Return the Markdown only.`,
  { label: 'synthesize', phase: 'Synthesize' },
)

return { reports: clean, summary }
