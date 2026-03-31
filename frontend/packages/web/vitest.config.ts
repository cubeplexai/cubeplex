import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    exclude: ['__tests__/e2e/**', 'node_modules/**'],
  },
  resolve: {
    conditions: ['source', 'import', 'module', 'browser', 'default'],
  },
})
