import '@testing-library/jest-dom'
import { vi } from 'vitest'

// @lobehub/icons transitively imports @emoji-mart/data JSON that Vitest cannot
// load (ERR_IMPORT_ATTRIBUTE_MISSING). Shared brand map is the single entry
// point used by ModelBrandLogo and admin ProviderLogo.
vi.mock('@/lib/models/brand-icons', () => ({
  BRAND_ICONS: {},
}))
