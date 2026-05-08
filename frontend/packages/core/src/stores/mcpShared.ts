import { ApiError } from '../api/client'

export interface CatalogErrorEnvelope {
  code: string
  message: string
}

export function toCatalogError(err: unknown): CatalogErrorEnvelope {
  if (err instanceof ApiError) {
    return { code: err.code ?? 'unknown', message: err.message }
  }
  return { code: 'unknown', message: (err as Error).message ?? 'Unknown error' }
}
