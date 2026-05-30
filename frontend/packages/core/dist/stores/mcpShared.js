import { ApiError } from '../api/client';
export function toCatalogError(err) {
    if (err instanceof ApiError) {
        return { code: err.code ?? 'unknown', message: err.message };
    }
    return { code: 'unknown', message: err.message ?? 'Unknown error' };
}
//# sourceMappingURL=mcpShared.js.map