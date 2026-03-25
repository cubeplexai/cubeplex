export interface ApiClient {
  baseUrl: string
  get(path: string): Promise<Response>
  post(path: string, body: unknown): Promise<Response>
}

export function createApiClient(baseUrl: string): ApiClient {
  return {
    baseUrl,
    get: (path: string) => fetch(`${baseUrl}${path}`),
    post: (path: string, body: unknown) =>
      fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
  }
}

export async function toApiError(res: Response): Promise<Error> {
  const contentType = res.headers.get('content-type')
  if (contentType?.includes('application/json')) {
    const data = await res.json() as { message?: string }
    return new Error(data.message || `HTTP ${res.status}`)
  }
  return new Error(`HTTP ${res.status}: ${res.statusText}`)
}
