import type { ApiClient } from './client';
export interface TestStreamEvent {
    event: 'liveness' | 'model' | 'done';
    data: unknown;
}
export declare function parseTestStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<TestStreamEvent>;
export declare function startTestStream(client: ApiClient, providerId: string, modelDbIds: string[]): Promise<ReadableStream<Uint8Array>>;
//# sourceMappingURL=providerTestStream.d.ts.map