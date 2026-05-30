import type { AgentEvent, Message } from '../types';
import { type ApiClient } from './client';
export interface ActiveRunBootstrap {
    run_id: string;
    status: string;
    user_message?: string | null;
    last_event_id?: string | null;
    started_at?: string | null;
}
export interface ConversationBootstrap {
    messages: Message[];
    total: number;
    active_run: ActiveRunBootstrap | null;
    last_run_status: 'stale' | null;
    usage_summary?: {
        turn?: {
            input_tokens: number;
            output_tokens: number;
            cache_read_tokens: number;
            cache_write_tokens: number;
        };
        session: {
            total_input_tokens: number;
            total_output_tokens: number;
            total_cache_read_tokens: number;
            total_cache_write_tokens: number;
        };
        context_window: number;
    };
}
export interface StartRunResponse {
    run_id: string;
}
export declare function getConversationBootstrap(client: ApiClient, conversationId: string): Promise<ConversationBootstrap>;
export declare function startMessageRun(client: ApiClient, conversationId: string, content: string, attachmentIds?: string[]): Promise<StartRunResponse>;
export declare function streamRun(client: ApiClient, conversationId: string, runId: string, lastEventId?: string, signal?: AbortSignal): AsyncGenerator<AgentEvent>;
//# sourceMappingURL=runStreams.d.ts.map