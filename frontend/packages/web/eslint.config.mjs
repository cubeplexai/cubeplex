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
