# Login Page Redesign Implementation Plan

**Goal:** Replace the bare login form with a polished cubebox entry screen while preserving every existing auth flow.

**Architecture:** Keep routing and API behavior unchanged. Use the auth layout as the visual shell and keep `LoginForm` as the stateful client component for password, Google, SSO, and verification flows.

**Tech Stack:** Next.js App Router, React 19, Tailwind v4 semantic tokens, next-intl, existing auth helpers from `@cubebox/core`.

## Constraints

- Preserve `/login?next=...` redirect handling.
- Preserve SSO-required and email-not-verified fallback behavior.
- Preserve single-tenant SSO behavior from `useDeploymentMode()`.
- Use existing project tokens: Geist, neutral surfaces, `bg-primary`, `text-primary`.
- No new dependencies.
- No route, API, or field-name changes.

## Tasks

1. Add a focused component test for the redesigned login surface.
   - Assert the product narrative copy renders.
   - Assert the form still exposes accessible Email, Password, Sign in, Google, SSO, forgot-password, and create-account controls.

2. Redesign the auth layout.
   - Convert `(auth)/layout.tsx` from centered single card to a responsive two-column shell.
   - Add product-specific copy about agents, tools, memory, and workspace control.
   - Keep mobile single-column and make the form visible without horizontal overflow.

3. Redesign `LoginForm`.
   - Replace raw loose spacing with a structured panel, accessible labels, stronger focus states, and clearer error/callout surfaces.
   - Keep all existing state and submit logic intact.
   - Update third-party auth buttons only as needed for visual consistency.

4. Update localized auth copy.
   - Improve login title/subtitle and provider button wording in English and Chinese.
   - Avoid em-dashes and generic SaaS filler.

5. Verify.
   - Run the new component test red first, then green.
   - Run targeted frontend tests for login/SSO behavior where possible.
   - Run `pnpm type-check`.
   - Start the frontend dev server on the worktree port and inspect `/login`.
