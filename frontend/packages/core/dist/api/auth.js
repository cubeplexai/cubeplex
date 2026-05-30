import { toApiError } from './client';
export async function registerUser(client, email, password) {
    const res = await client.post('/api/v1/auth/register', { email, password });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function loginUser(client, email, password) {
    const res = await client.postForm('/api/v1/auth/login', {
        username: email,
        password,
    });
    if (!res.ok)
        throw await toApiError(res);
}
export async function logoutUser(client) {
    const res = await client.post('/api/v1/auth/logout', {});
    if (!res.ok && res.status !== 401)
        throw await toApiError(res);
}
export async function getMe(client) {
    const res = await client.get('/api/v1/auth/me');
    if (res.status === 401)
        return null;
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
export async function updateLanguage(client, language) {
    const res = await client.patch('/api/v1/auth/me', { language });
    if (!res.ok)
        throw await toApiError(res);
    return (await res.json());
}
//# sourceMappingURL=auth.js.map