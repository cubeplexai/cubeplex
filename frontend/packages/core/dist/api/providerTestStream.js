import { toApiError } from './client';
export async function* parseTestStream(stream) {
    const reader = stream.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let curEvent = '';
    for (;;) {
        const { value, done } = await reader.read();
        if (done)
            break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, i).trimEnd();
            buf = buf.slice(i + 1);
            if (line.startsWith('event: ')) {
                curEvent = line.slice(7);
            }
            else if (line.startsWith('data: ')) {
                yield { event: curEvent, data: JSON.parse(line.slice(6)) };
            }
        }
    }
}
export async function startTestStream(client, providerId, modelDbIds) {
    const res = await client.postRaw(`/api/v1/admin/providers/${providerId}/test/stream`, { model_db_ids: modelDbIds }, { Accept: 'text/event-stream' });
    if (!res.ok || !res.body)
        throw await toApiError(res);
    return res.body;
}
//# sourceMappingURL=providerTestStream.js.map