import type { AgentEvent } from '../types';
import type { ApiClient } from './client';
export interface CancelRunResponse {
    status: 'cancelled' | 'published' | 'no_active_run';
    run_id: string | null;
}
export declare function cancelActiveRun(client: ApiClient, conversationId: string): Promise<CancelRunResponse>;
export interface SteerRunResponse {
    status: 'steered' | 'published' | 'no_active_run';
    run_id: string | null;
}
export declare function steerRun(client: ApiClient, conversationId: string, content: string, steerId: string): Promise<SteerRunResponse>;
export interface CancelSteerResponse {
    status: 'cancelled' | 'not_found' | 'published' | 'no_active_run';
    run_id: string | null;
}
export declare function cancelSteer(client: ApiClient, conversationId: string, steerId: string): Promise<CancelSteerResponse>;
export interface SandboxConfirmResponse {
    status: 'delivered' | 'published' | 'no_active_run';
    run_id: string | null;
}
export declare function submitSandboxConfirm(client: ApiClient, conversationId: string, questionId: string, decision: 'approve' | 'deny', reason?: string): Promise<SandboxConfirmResponse>;
export interface AskUserResponse {
    status: 'delivered' | 'published' | 'no_active_run';
    run_id: string | null;
}
export declare function submitAskUserAnswer(client: ApiClient, conversationId: string, questionId: string, answers: Record<string, string | string[]>): Promise<AskUserResponse>;
export declare function streamMessages(client: ApiClient, conversationId: string, content: string, attachmentIds?: string[], signal?: AbortSignal): AsyncGenerator<AgentEvent>;
//# sourceMappingURL=stream.d.ts.map