#!/usr/bin/env node
// Loads .worktree.env (if present) into process.env, then exec's the given
// command. This is the shell-level counterpart to the in-config dotenv calls
// in next.config.ts and playwright.config.ts — those handle SSR/rewrite vars,
// but `next dev` reads PORT *before* loading next.config.ts, so PORT (and
// any other variable consumed by a CLI before its config loads) must be in
// the process environment from the start.
//
// Usage: node scripts/with-worktree-env.mjs <command> [args...]
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'

const here = dirname(fileURLToPath(import.meta.url))
const envPath = resolve(here, '../../.worktree.env')
if (existsSync(envPath)) {
  dotenv.config({ path: envPath, override: false })
}

const [, , cmd, ...args] = process.argv
if (!cmd) {
  console.error('usage: with-worktree-env.mjs <command> [args...]')
  process.exit(2)
}

const child = spawn(cmd, args, { stdio: 'inherit', shell: false })
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  else process.exit(code ?? 0)
})
