import type { AgentEvent, ContentBlock, Message, TodoItem, ToolCallEvent, ToolResultEvent } from '../types';
import type { ApiClient } from '../api';
export interface AgentStream {
    text: string;
    toolCalls: ToolCallEvent[];
    toolResults: ToolResultEvent[];
    thinking: string;
    blocks: ContentBlock[];
    name: string | null;
}
export interface PendingConfirm {
    question_id: string;
    command: string;
    matched_pattern: string | null;
    timeout_seconds: number | null;
    requestedAt: number;
}
export interface PendingAsk {
    question_id: string;
    questions: import('../types/events').AskQuestion[];
    timeout_seconds: number | null;
    requestedAt: number;
}
export interface MessageStore {
    messages: Record<string, Message[]>;
    pendingSteers: Record<string, {
        steerId: string;
        text: string;
    }[]>;
    streamAgents: Record<string, AgentStream>;
    isStreaming: boolean;
    streamingConversationId: string | null;
    currentRunId: string | null;
    lastAppliedEventId: string | null;
    statusPhase: string | null;
    error: string | null;
    lastRunStatus: 'stale' | null;
    todos: TodoItem[];
    toolStartedMap: Record<string, number>;
    toolResultMap: Record<string, {
        content: string;
        receivedAt: number;
        startedAt?: number;
        contentType?: string;
    }>;
    turnUsage: Record<string, import('../types').TurnUsage | null>;
    sessionUsage: Record<string, import('../types').SessionUsage | null>;
    contextWindow: Record<string, number | null>;
    pendingConfirmMap: Record<string, PendingConfirm>;
    pendingAsk: PendingAsk | null;
    loadMessages(client: ApiClient, conversationId: string): Promise<void>;
    send(client: ApiClient, conversationId: string, content: string, attachmentIds?: string[], attachments?: import('../types').MessageAttachment[]): Promise<void>;
    cancelStream(client: ApiClient, conversationId: string): Promise<void>;
    steer(client: ApiClient, conversationId: string, content: string): Promise<void>;
    cancelSteer(client: ApiClient, conversationId: string, steerId: string): Promise<void>;
    __commitTurnAndInject(conversationId: string, data: {
        content: string;
        steer_id: string;
    }): void;
    clearStream(): void;
    clearLastRunStatus(): void;
    /** Test hook: apply a single AgentEvent synchronously */
    __applyEvent(event: AgentEvent): void;
}
/**
 * History returned by `/bootstrap` may contain checkpoints from the active run.
 * The stream replay re-emits all of that content, so anything after the active
 * run's user message would render twice. Trim history to end at that user
 * message — or append a pending placeholder if the run is so early that the
 * user message has not been checkpointed yet.
 *
 * `startedAt` (ISO from the run record) disambiguates a same-content user
 * message from a prior turn; we only bind to a history entry created at or
 * after the run was claimed.
 */
export declare function trimHistoryForActiveRun(messages: Message[], runId: string, content: string, startedAt: string | null): Message[];
export declare const useMessageStore: import("zustand").UseBoundStore<import("zustand").StoreApi<MessageStore>>;
//# sourceMappingURL=messageStore.d.ts.map