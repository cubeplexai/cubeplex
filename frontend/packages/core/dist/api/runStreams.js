import { toApiError } from './client';
import { CSRF_COOKIE_NAME } from './cookieNames';
async function* readLines(reader) {
    let buffer = '';
    const decoder = new TextDecoder();
    while (true) {
        const { done, value } = await reader.read();
        if (done)
            break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
            yield line;
        }
    }
    if (buffer)
        yield buffer;
}
function readCookie(name) {
    if (typeof document === 'undefined')
        return '';
    const match = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`));
    return match ? decodeURIComponent(match.slice(name.length + 1)) : '';
}
export async function getConversationBootstrap(client, conversationId) {
    const res = await client.get(`/api/v1/conversations/${conversationId}/bootstrap`);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function startMessageRun(client, conversationId, content, attachmentIds) {
    const body = { content };
    if (attachmentIds && attachmentIds.length)
        body.attachments = attachmentIds;
    const res = await client.post(`/api/v1/conversations/${conversationId}/messages`, body);
    if (!res.ok)
        throw await toApiError(res);
    return res.json();
}
export async function* streamRun(client, conversationId, runId, lastEventId, signal) {
    const headers = {
        Accept: 'text/event-stream',
        'Cache-Control': 'no-cache',
    };
    if (lastEventId)
        headers['Last-Event-ID'] = lastEventId;
    const csrf = readCookie(CSRF_COOKIE_NAME);
    if (csrf)
        headers['X-CSRF-Token'] = csrf;
    const path = client.resolvePath(`/api/v1/conversations/${conversationId}/runs/${runId}/stream`);
    let res;
    try {
        res = await fetch(`${client.baseUrl}${path}`, {
            method: 'GET',
            credentials: 'include',
            headers,
            cache: 'no-store',
            signal,
        });
    }
    catch (err) {
        if (err.name === 'AbortError')
            return;
        throw err;
    }
    if (!res.ok || !res.body) {
        throw await toApiError(res);
    }
    const reader = res.body.getReader();
    try {
        for await (const line of readLines(reader)) {
            if (!line.startsWith('data: '))
                continue;
            try {
                yield JSON.parse(line.slice(6));
            }
            catch {
                // Ignore malformed lines and keep the stream alive.
            }
        }
    }
    catch (err) {
        if (err.name !== 'AbortError')
            throw err;
    }
    finally {
        reader.releaseLock();
    }
}
//# sourceMappingURL=runStreams.js.map