import type { PanelContentType, ToolCallRef } from '../types';
export interface AttachmentPanelInfo {
    attachmentId: string;
    filename: string;
    downloadUrl: string;
    mimeType: string;
    sizeBytes: number;
}
export type PanelView = {
    type: 'closed';
} | {
    type: 'tool';
    toolName: string;
    toolArgs: Record<string, unknown>;
    toolResult: string | null;
    contentType: PanelContentType;
    toolRef: ToolCallRef | null;
    highlightText: string | null;
    highlightKey: number;
} | {
    type: 'artifact';
    conversationId: string;
    artifactId: string;
} | {
    type: 'attachment';
    info: AttachmentPanelInfo;
} | {
    type: 'browser';
};
export interface PanelStore {
    view: PanelView;
    openTool: (toolName: string, toolArgs: Record<string, unknown>, toolResult: string | null, contentType?: string, toolRef?: ToolCallRef, highlightText?: string) => void;
    openArtifact: (conversationId: string, artifactId: string) => void;
    openAttachment: (info: AttachmentPanelInfo) => void;
    openBrowser: () => void;
    close: () => void;
}
export declare const usePanelStore: import("zustand").UseBoundStore<import("zustand").StoreApi<PanelStore>>;
//# sourceMappingURL=panelStore.d.ts.map