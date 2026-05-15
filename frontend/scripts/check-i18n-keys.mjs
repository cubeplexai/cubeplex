#!/usr/bin/env node
// i18n key consistency checker.
//
// What this catches that `tsc` does not:
//   1. Locale drift — a key exists in en.json but is missing from zh.json
//      (or vice versa). `next-intl`'s typed `AppConfig.Messages` interface is
//      built from en.json, so type-check only protects en parity.
//   2. Dynamic `t(...)` calls — template-literal or computed keys bypass the
//      next-intl key type entirely, and `as 'literal'` casts silence it. This
//      is exactly the shape that produced the `mcpCatalog.authOauth` bug
//      (computed key + cast). Reported as a warning with a fix suggestion.
//
// Limitations:
//   - We don't validate that dynamic keys *do* exist (would need taint/scope
//     analysis). We surface them so a human can move them to a literal map.

import { promises as fs } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND_ROOT = path.resolve(__dirname, '..')
const MESSAGES_DIR = path.join(FRONTEND_ROOT, 'packages/web/messages')
const SOURCE_ROOT = path.join(FRONTEND_ROOT, 'packages/web')

const SOURCE_EXTS = new Set(['.ts', '.tsx'])
const SKIP_DIRS = new Set(['node_modules', '.next', 'dist', 'test-results', 'playwright-report'])

function flatten(obj, prefix, out) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      flatten(v, key, out)
    } else {
      out.add(key)
    }
  }
  return out
}

async function loadLocale(name) {
  const file = path.join(MESSAGES_DIR, `${name}.json`)
  const raw = await fs.readFile(file, 'utf8')
  return { name, keys: flatten(JSON.parse(raw), '', new Set()), file }
}

async function* walk(dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true })
  for (const e of entries) {
    if (SKIP_DIRS.has(e.name)) continue
    const full = path.join(dir, e.name)
    if (e.isDirectory()) {
      yield* walk(full)
    } else if (SOURCE_EXTS.has(path.extname(e.name))) {
      yield full
    }
  }
}

// Match `useTranslations(...)` to identify files that use next-intl, then
// scan for `<var>(<non-string-literal>)` calls on the same bound name.
const USE_TRANSLATIONS_RE =
  /(?:const|let|var)\s+(\w+)\s*=\s*useTranslations\s*\(/g

// Dynamic = first arg is not a plain string literal. Skip lines that are
// just a literal `t('foo')` or `t("foo")`.
function buildDynamicCallRe(varName) {
  // Match `varName(` followed by something that is NOT a quote (so not a
  // pure literal call). Stop at the matching ) — naive but good enough.
  return new RegExp(`\\b${varName}\\(\\s*([^'"\\s][^)]*)\\)`, 'g')
}

// Match `t(<anything> as '<key>')` — any key argument cast to a string
// literal. This silences next-intl's typed-key check and is the exact
// shape that produced the mcpCatalog.authOauth bug
// (`tc(`auth${x}` as 'authOAuth')`). Matches both plain literals and
// template-literal/expression arguments.
function buildCastCallRe(varName) {
  return new RegExp(`\\b${varName}\\(\\s*[^)]*?\\s+as\\s+['"\`][^'"\`]+['"\`]\\s*\\)`, 'g')
}

function indexToLine(text, index) {
  let line = 1
  for (let i = 0; i < index && i < text.length; i++) {
    if (text.charCodeAt(i) === 10) line++
  }
  return line
}

async function scanFile(file) {
  const text = await fs.readFile(file, 'utf8')
  if (!text.includes('useTranslations(')) return { dynamic: [], casts: [] }

  const varNames = new Set()
  for (const m of text.matchAll(USE_TRANSLATIONS_RE)) varNames.add(m[1])
  if (varNames.size === 0) return { dynamic: [], casts: [] }

  const dynamic = []
  const casts = []
  for (const varName of varNames) {
    for (const m of text.matchAll(buildDynamicCallRe(varName))) {
      dynamic.push({ file, line: indexToLine(text, m.index), snippet: m[0].trim() })
    }
    for (const m of text.matchAll(buildCastCallRe(varName))) {
      casts.push({ file, line: indexToLine(text, m.index), snippet: m[0].trim() })
    }
  }
  return { dynamic, casts }
}

async function main() {
  const errors = []
  const warnings = []

  // 1. Locale parity.
  const messageFiles = (await fs.readdir(MESSAGES_DIR)).filter((f) => f.endsWith('.json'))
  if (!messageFiles.includes('en.json')) {
    console.error('FATAL: en.json missing — cannot establish reference key set')
    process.exit(2)
  }
  const locales = await Promise.all(messageFiles.map((f) => loadLocale(path.basename(f, '.json'))))
  const en = locales.find((l) => l.name === 'en')
  for (const loc of locales) {
    if (loc.name === 'en') continue
    const onlyInEn = [...en.keys].filter((k) => !loc.keys.has(k))
    const onlyInLoc = [...loc.keys].filter((k) => !en.keys.has(k))
    if (onlyInEn.length) {
      errors.push(
        `Locale '${loc.name}' is missing ${onlyInEn.length} key(s) present in 'en':\n` +
          onlyInEn.map((k) => `    - ${k}`).join('\n'),
      )
    }
    if (onlyInLoc.length) {
      errors.push(
        `Locale '${loc.name}' has ${onlyInLoc.length} key(s) not present in 'en':\n` +
          onlyInLoc.map((k) => `    - ${k}`).join('\n'),
      )
    }
  }

  // 2. Dynamic-call and cast warnings.
  const allDynamic = []
  const allCasts = []
  for await (const file of walk(SOURCE_ROOT)) {
    const { dynamic, casts } = await scanFile(file)
    allDynamic.push(...dynamic)
    allCasts.push(...casts)
  }
  if (allCasts.length) {
    // `t('foo' as 'bar')` masks the next-intl key check — this is the exact
    // shape that produced the mcpCatalog.authOauth bug. Treat as an error.
    const lines = allCasts.map(
      (c) => `    ${path.relative(FRONTEND_ROOT, c.file)}:${c.line} — ${c.snippet}`,
    )
    errors.push(
      `${allCasts.length} t(...) call(s) cast their key with 'as' — these bypass next-intl's typed-key check. ` +
        `Remove the cast (let the next-intl type guide the literal) or build a static Record<Enum, MessageKey> map:\n` +
        lines.join('\n'),
    )
  }
  if (allDynamic.length) {
    const lines = allDynamic.map(
      (d) => `    ${path.relative(FRONTEND_ROOT, d.file)}:${d.line} — ${d.snippet}`,
    )
    warnings.push(
      `${allDynamic.length} dynamic t(...) call(s) detected — these bypass next-intl's typed-key check. ` +
        `Prefer an explicit Record<EnumLike, MessageKey> map so a typo fails type-check:\n` +
        lines.join('\n'),
    )
  }

  if (warnings.length) for (const w of warnings) console.warn(`warning: ${w}`)
  if (errors.length) {
    for (const e of errors) console.error(`error: ${e}`)
    process.exit(1)
  }
  console.log(
    `i18n: ${en.keys.size} keys × ${locales.length} locale(s) — parity ok` +
      (allDynamic.length ? `; ${allDynamic.length} dynamic call(s) (warning)` : ''),
  )
}

main().catch((err) => {
  console.error(err)
  process.exit(2)
})
