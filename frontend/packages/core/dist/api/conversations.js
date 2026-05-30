import { toApiError } from './client';
export async function createConversation(client, title, opts = {}) {
    const params = new URLSearchParams();
    if (title)
        params.set('title', title);
    if (opts.draft)
        params.set('draft', 'true');
    const qs = params.toString();
    const url = qs ? `/api/v1/conversations?${qs}` : '/api/v1/conversations';
    const res = await client.post(url, {});
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function listConversations(client, limit = 50, offset = 0) {
    const url = `/api/v1/conversations?limit=${limit}&offset=${offset}`;
    const res = await client.get(url);
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.conversations || [];
}
export async function getConversation(client, id) {
    const res = await client.get(`/api/v1/conversations/${id}`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function deleteConversation(client, id) {
    // Backend route is `@router.delete("/{conversation_id}")`. There is no
    // method-override middleware, so we call DELETE directly.
    const res = await client.del(`/api/v1/conversations/${id}`);
    if (!res.ok)
        throw await toApiError(res);
}
export async function renameConversation(client, id, title) {
    // Backend route is `@router.patch("/{conversation_id}")` with `title` as a
    // query parameter, not a body. There is no method-override middleware,
    // so we call PATCH directly.
    const res = await client.patch(`/api/v1/conversations/${id}?title=${encodeURIComponent(title)}`, {});
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function setPinConversation(client, id, isPinned) {
    const res = await client.patch(`/api/v1/conversations/${id}/pin`, {
        is_pinned: isPinned,
    });
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function generateConversationTitle(client, id, content) {
    const res = await client.post(`/api/v1/conversations/${id}/generate-title`, { content });
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function listMessages(client, conversationId, limit = 50, offset = 0) {
    const url = `/api/v1/conversations/${conversationId}/messages?limit=${limit}&offset=${offset}`;
    const res = await client.get(url);
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.messages || [];
}
export async function listArtifacts(client, conversationId) {
    const url = `/api/v1/conversations/${conversationId}/artifacts`;
    const res = await client.get(url);
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.artifacts || [];
}
export async function listArtifactVersions(client, conversationId, artifactId) {
    const url = `/api/v1/conversations/${conversationId}/artifacts/${artifactId}/versions`;
    const res = await client.get(url);
    if (!res.ok)
        throw await toApiError(res);
    const data = (await res.json());
    return data.versions || [];
}
export async function requestPreviewToken(client, conversationId, artifactId, version) {
    const params = version != null ? `?version=${version}` : '';
    const url = `/api/v1/conversations/${conversationId}/artifacts/${artifactId}/preview-token${params}`;
    const res = await client.post(url, {});
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
//# sourceMappingURL=conversations.js.map