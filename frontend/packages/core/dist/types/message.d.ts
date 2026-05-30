import type { CitationData } from './citation';
import type { ContentBlock } from './events';
export interface SubagentToolResult {
    tool_name: string;
    tool_call_id: string;
    content: string;
    content_type?: string | null;
    started_at?: string | null;
    completed_at?: string | null;
}
export interface SubagentSummary {
    text: string;
    tool_calls: {
        name: string;
        arguments: Record<string, unknown>;
        id?: string;
        started_at?: string | null;
    }[];
    tool_results?: SubagentToolResult[];
    thinking: string;
    role?: string;
    task?: string;
}
export interface MessageAttachment {
    file_id: string;
    filename: string;
    kind: 'image' | 'document' | 'other';
    size_bytes: number;
    width?: number | null;
    height?: number | null;
    thumbnail_url?: string | null;
    download_url?: string | null;
}
export interface MessageUsage {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens?: number;
    cache_write_tokens?: number;
}
interface MessageBase {
    id: string;
    timestamp?: number | null;
    metadata?: Record<string, unknown> & {
        attachments?: MessageAttachment[];
        memory_snapshot?: unknown;
        citations?: CitationData[];
        subagent_events?: SubagentSummary;
        steer_id?: string;
    };
}
export interface UserMessage extends MessageBase {
    role: 'user';
    content: ContentBlock[];
}
export interface AssistantMessage extends MessageBase {
    role: 'assistant';
    content: ContentBlock[];
    stop_reason?: string;
    error_message?: string | null;
    usage?: MessageUsage | null;
    provider_id?: string;
    model_id?: string;
    response_id?: string | null;
}
export interface ToolResultMessage extends MessageBase {
    role: 'tool_result';
    tool_call_id: string;
    tool_name: string;
    content: ContentBlock[];
    is_error?: boolean;
    details?: unknown;
}
export type Message = UserMessage | AssistantMessage | ToolResultMessage;
export declare function getTextContent(msg: Message): string;
/**
 * Content to feed tool-result previews (SearchResultView / WebFetchView, citation
 * popovers). CitationMiddleware rewrites a tool result's `.content` to 【N-M】-marked
 * chunk text for the LLM and stashes the raw, parseable output in
 * `details.original_content` (see backend cubebox/middleware/citation.py). Previews
 * need that raw output — falling back to `.content` would feed them the citation
 * markup, which they can't parse. The live SSE path already prefers original_content
 * (cubebox/agents/stream.py `_stringify_tool_result`); this keeps reload consistent.
 */
export declare function getToolResultPreviewContent(msg: ToolResultMessage): string;
export declare function getThinking(msg: AssistantMessage): string;
export declare function getToolCalls(msg: AssistantMessage): Extract<ContentBlock, {
    type: 'tool_call';
}>[];
/**
 * Extract a SubagentSummary for a tool result message, handling both shapes:
 *
 *   - in-memory after live finalization: `metadata.subagent_events` already
 *     holds a normalized `SubagentSummary`
 *   - reloaded from cubepi: `details.subagent_events` holds the raw SSE event
 *     list collected by `SubAgentMiddleware` — we replay it into a summary
 *
 * Returns null when neither shape is present.
 */
export declare function getSubagentSummary(msg: ToolResultMessage): SubagentSummary | null;
export {};
//# sourceMappingURL=message.d.ts.map