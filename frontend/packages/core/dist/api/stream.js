import { CSRF_COOKIE_NAME } from './cookieNames';
import { streamRun } from './runStreams';
export async function cancelActiveRun(client, conversationId) {
    const res = await client.post(`/api/v1/conversations/${conversationId}/cancel`, {});
    if (!res.ok) {
        throw new Error(`Failed to cancel run: HTTP ${res.status}`);
    }
    return (await res.json());
}
export async function steerRun(client, conversationId, content, steerId) {
    const res = await client.post(`/api/v1/conversations/${conversationId}/steer`, {
        content,
        steer_id: steerId,
    });
    if (!res.ok) {
        throw new Error(`Failed to steer run: HTTP ${res.status}`);
    }
    return (await res.json());
}
export async function cancelSteer(client, conversationId, steerId) {
    const res = await client.post(`/api/v1/conversations/${conversationId}/steer/cancel`, {
        steer_id: steerId,
    });
    if (!res.ok) {
        throw new Error(`Failed to cancel steer: HTTP ${res.status}`);
    }
    return (await res.json());
}
export async function submitSandboxConfirm(client, conversationId, questionId, decision, reason) {
    const res = await client.post(`/api/v1/conversations/${conversationId}/sandbox-confirm/${questionId}`, { decision, reason: reason ?? null });
    if (!res.ok) {
        throw new Error(`Failed to submit sandbox confirm: HTTP ${res.status}`);
    }
    return (await res.json());
}
export async function submitAskUserAnswer(client, conversationId, questionId, answers) {
    const res = await client.post(`/api/v1/conversations/${conversationId}/ask-user/${questionId}`, {
        answers,
    });
    if (!res.ok) {
        throw new Error(`Failed to submit ask_user answer: HTTP ${res.status}`);
    }
    return (await res.json());
}
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
export async function* streamMessages(client, conversationId, content, attachmentIds, signal) {
    const headers = {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        'Cache-Control': 'no-cache',
    };
    const csrf = readCookie(CSRF_COOKIE_NAME);
    if (csrf)
        headers['X-CSRF-Token'] = csrf;
    const path = client.resolvePath(`/api/v1/conversations/${conversationId}/messages`);
    const requestBody = { content };
    if (attachmentIds && attachmentIds.length)
        requestBody.attachments = attachmentIds;
    try {
        const res = await fetch(`${client.baseUrl}${path}`, {
            method: 'POST',
            credentials: 'include',
            headers,
            cache: 'no-store',
            body: JSON.stringify(requestBody),
            signal,
        });
        if (!res.ok) {
            yield {
                type: 'error',
                timestamp: new Date().toISOString(),
                data: { message: `HTTP ${res.status}` },
                agent_id: null,
                agent_name: null,
            };
            return;
        }
        const contentType = res.headers.get('content-type') ?? '';
        if (contentType.includes('text/event-stream')) {
            const reader = res.body?.getReader();
            if (!reader)
                return;
            try {
                for await (const line of readLines(reader)) {
                    if (line.startsWith('data: ')) {
                        try {
                            yield JSON.parse(line.slice(6));
                        }
                        catch {
                            // skip malformed lines
                        }
                    }
                }
            }
            finally {
                reader.releaseLock();
            }
            return;
        }
        const body = (await res.json());
        for await (const event of streamRun(client, conversationId, body.run_id, undefined, signal)) {
            yield event;
        }
    }
    catch (err) {
        if (err.name === 'AbortError')
            return;
        yield {
            type: 'error',
            timestamp: new Date().toISOString(),
            data: { message: 'Connection lost' },
            agent_id: null,
            agent_name: null,
        };
    }
}
//# sourceMappingURL=stream.js.map