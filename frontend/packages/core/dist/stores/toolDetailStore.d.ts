import type { PanelContentType, ToolCallRef } from '../types';
export interface ToolDetailStore {
    isOpen: boolean;
    toolName: string;
    toolArgs: Record<string, unknown>;
    toolResult: string | null;
    contentType: PanelContentType;
    toolRef: ToolCallRef | null;
    highlightText: string | null;
    highlightKey: number;
    open: (toolName: string, toolArgs: Record<string, unknown>, toolResult: string | null, contentType?: string, toolRef?: ToolCallRef, highlightText?: string) => void;
    close: () => void;
}
export declare const useToolDetailStore: (<T>(selector: (s: ToolDetailStore) => T) => T) & {
    getState(): ToolDetailStore;
};
//# sourceMappingURL=toolDetailStore.d.ts.map