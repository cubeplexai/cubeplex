import nextCoreWebVitals from 'eslint-config-next/core-web-vitals'
import nextTypescript from 'eslint-config-next/typescript'

// --- redesign color guard (docs/dev/specs/2026-06-10-ui-redesign-design.md §1)
const RAW_PALETTE =
  '(?:bg|text|border|ring|divide|from|to)-(?:amber|blue|green|red|emerald|sky|yellow|purple|pink|orange|indigo|violet|teal|cyan|lime|rose|fuchsia|slate|gray|zinc|neutral|stone)-[0-9]'

const rawColorGuard = {
  files: ['components/**/*.tsx', 'app/**/*.tsx'],
  ignores: [
    'components/chat/widget/**', // iframe srcdoc: literal hex is structural (see spec)
    // TEMP ALLOWLIST — existing offenders; each redesign stage deletes the
    // files it cleans; MUST be empty by Stage 7 (plan Task 7.2).
    'app/admin/sandbox/_components/CommandRulesTable.tsx',
    'app/admin/sandbox/_components/CredentialConflictBanner.tsx',
    'app/admin/sandbox/_components/PolicyEditor.tsx',
    'app/(app)/w/\\[wsId\\]/memory/components/MemoryItemCard.tsx',
    'app/(app)/w/\\[wsId\\]/sandbox/_components/SandboxStatusCard.tsx',
    'app/(app)/w/\\[wsId\\]/scheduled-tasks/components/ScheduledTaskCard.tsx',
    'app/(app)/w/\\[wsId\\]/scheduled-tasks/components/ScheduledTaskRunsPanel.tsx',
    'app/(app)/w/\\[wsId\\]/scheduled-tasks/components/ScheduleEditor.tsx',
    'components/admin/insights/cost/KpiRow.tsx',
    'components/admin/models/ModelRow.tsx',
    'components/admin/models/ProviderConfigForm.tsx',
    'components/admin/models/ProviderDetail.tsx',
    'components/admin/models/ProviderLogo.tsx',
    'components/admin/models/ReadinessBadge.tsx',
    'components/admin/models/__tests__/ReadinessBadge.test.tsx',
    'components/admin/models/wizard/LivenessRow.tsx',
    'components/admin/models/wizard/ModelTestCard.tsx',
    'components/admin/settings/OrgLLMSettingsCard.tsx',
    'components/admin/skill-registries/RegistryCard.tsx',
    'components/admin/skills/AdminCandidateDetailPanel.tsx',
    'components/admin/skills/SkillCard.tsx',
    'components/admin/skills/SkillDetailPanel.tsx',
    'components/admin/skills/UploadSkillModal.tsx',
    'components/auth/LoginForm.tsx',
    'components/auth/RegisterForm.tsx',
    'components/chat/ArtifactCard.tsx',
    'components/chat/AskUserCard.tsx',
    'components/chat/CitationMarker.tsx',
    'components/chat/FailoverBanner.tsx',
    'components/chat/FileChip.tsx',
    'components/chat/MessageList.tsx',
    'components/chat/SandboxConfirmCard.tsx',
    'components/chat/SubAgentCard.tsx',
    'components/chat/SubAgentCluster.tsx',
    'components/chat/TaskProgressBar.tsx',
    'components/chat/TaskProgressCard.tsx',
    'components/chat/ThinkingBadge.tsx',
    'components/chat/TokenUsageBar.tsx',
    'components/chat/ToolCallItem.tsx',
    'components/chat/tool-results/SkillCandidateCard.tsx',
    'components/mcp/AuthBandFrame.tsx',
    'components/mcp/MCPAdminDetailPanel.tsx',
    'components/mcp/MCPCitationEditor.tsx',
    'components/mcp/MCPCitationsTab.tsx',
    'components/mcp/MCPConnectorList.tsx',
    'components/mcp/MCPCustomCreatePanel.tsx',
    'components/panel/artifact/SkillArtifactPreview.tsx',
    'components/panel/BrowserView.tsx',
    'components/panel/FileReadView.tsx',
    'components/panel/GenericToolView.tsx',
    'components/panel/PanelHeader.tsx',
    'components/panel/SkillView.tsx',
    'components/panel/WriteFilePreviewView.tsx',
    'components/sandbox-env/EnvTable.tsx',
    'components/sandbox-env/WarningCell.tsx',
    'components/skills/CandidateCard.tsx',
    'components/skills/CandidateDetailPanel.tsx',
    'components/triggers/CopyIngestUrl.tsx',
    'components/triggers/TriggerDetailPanel.tsx',
    'components/triggers/TriggersList.tsx',
    'components/workspace-settings/McpPanel.tsx',
    'components/workspace-settings/skills/UploadWorkspaceSkillModal.tsx',
    'components/workspace-settings/skills/WorkspaceSkillCard.tsx',
    'components/workspace-settings/skills/WorkspaceSkillDetail.tsx',
    'components/workspace/WorkspaceCreateForm.tsx',
  ],
  rules: {
    'no-restricted-syntax': [
      'error',
      {
        selector: `Literal[value=/${RAW_PALETTE}/]`,
        message: 'Raw palette utilities are banned — use semantic tokens (spec §1).',
      },
      {
        selector: `TemplateElement[value.raw=/${RAW_PALETTE}/]`,
        message: 'Raw palette utilities are banned — use semantic tokens (spec §1).',
      },
    ],
  },
}

const config = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    ignores: [
      'node_modules/**',
      '.next/**',
      'dist/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      'public/**',
      '**/*.min.js',
      '**/*.min.mjs',
      'next-env.d.ts',
    ],
  },
  // Downgraded from error → warn to unblock the initial CI baseline.
  // To re-tighten: fix each underlying warning, then change the value back to 'error'
  // and restore `--max-warnings=0` on `web`'s `lint` script in package.json.
  // Current baseline count: ~16 warnings in production code (see `pnpm --filter web lint`).
  {
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@next/next/no-img-element': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/static-components': 'warn',
    },
  },
  {
    files: ['**/*.test.ts', '**/*.test.tsx', '**/__tests__/**'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
  rawColorGuard,
]

export default config
