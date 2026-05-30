import { type ApiClient } from './client';
export interface Trigger {
    id: string;
    name: string;
    enabled: boolean;
    source_type: string;
    source_config: Record<string, unknown>;
    target_type: string;
    target_ref: Record<string, unknown>;
    payload_fields: string[];
    filter: Record<string, unknown> | null;
    conversation_policy: string;
    run_as_user_id: string;
    max_runs_per_minute: number;
    rate_limit_burst: number;
    rate_limit_response: '429' | '202_drop';
    current_secret_cred_id: string;
    previous_secret_cred_id: string | null;
    previous_secret_expires_at: string | null;
    events_total: number;
    events_success: number;
    events_failed: number;
    events_dedup_dropped: number;
    created_at: string;
    updated_at: string;
}
export interface TriggerEvent {
    id: string;
    trigger_id: string;
    source_type: string;
    event_type: string | null;
    dedup_key: string;
    occurred_at: string | null;
    received_at: string;
    status: string;
    attempts: number;
    last_error: string | null;
    payload: Record<string, unknown>;
    resulting_run_id: string | null;
    resulting_conversation_id: string | null;
}
export interface CreateTriggerBody {
    name: string;
    webhook_secret: string;
    prompt_template: string;
    payload_fields: string[];
    run_as_user_id: string;
    filter?: Record<string, unknown> | null;
    source_config?: Record<string, unknown>;
    max_runs_per_minute?: number;
    rate_limit_burst?: number;
    rate_limit_response?: '429' | '202_drop';
    conversation_policy?: 'new_each_time';
    target_type?: 'inline';
    source_type?: 'webhook';
    enabled?: boolean;
}
export interface UpdateTriggerBody {
    name?: string;
    enabled?: boolean;
    prompt_template?: string;
    payload_fields?: string[];
    filter?: Record<string, unknown> | null;
    run_as_user_id?: string;
    source_config?: Record<string, unknown>;
    max_runs_per_minute?: number;
    rate_limit_burst?: number;
    rate_limit_response?: '429' | '202_drop';
}
export interface RotateSecretBody {
    new_webhook_secret: string;
    overlap_seconds?: number;
}
export interface RotateSecretResult {
    previous_secret_expires_at: string | null;
    current_secret_cred_id: string;
}
export interface ListTriggerEventsQuery {
    status?: string;
    limit?: number;
    offset?: number;
}
export declare function listTriggers(client: ApiClient, wsId: string): Promise<Trigger[]>;
export declare function createTrigger(client: ApiClient, wsId: string, body: CreateTriggerBody): Promise<Trigger>;
export declare function getTrigger(client: ApiClient, wsId: string, id: string): Promise<Trigger>;
export declare function updateTrigger(client: ApiClient, wsId: string, id: string, patch: UpdateTriggerBody): Promise<Trigger>;
export declare function deleteTrigger(client: ApiClient, wsId: string, id: string): Promise<void>;
export declare function rotateSecret(client: ApiClient, wsId: string, id: string, body: RotateSecretBody): Promise<RotateSecretResult>;
export declare function listTriggerEvents(client: ApiClient, wsId: string, id: string, query?: ListTriggerEventsQuery): Promise<TriggerEvent[]>;
export declare function replayEvent(client: ApiClient, wsId: string, id: string, eventId: string): Promise<TriggerEvent>;
//# sourceMappingURL=triggers.d.ts.map