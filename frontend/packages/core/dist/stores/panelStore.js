// frontend/packages/core/src/stores/panelStore.ts
import { create } from 'zustand';
import { bareToolName } from '../lib/toolName';
/** Map tool name + optional backend content_type to a PanelContentType. */
function mapContentType(toolName, backendContentType) {
    const bare = bareToolName(toolName);
    if (bare === 'load_skill')
        return 'skill';
    if (bare === 'execute')
        return 'terminal';
    if (bare === 'write_file')
        return 'write_file';
    if (bare === 'code_execute' || bare === 'python')
        return 'code_execute';
    if (bare === 'file_read')
        return 'file_read';
    if (backendContentType === 'file_read')
        return 'file_read';
    if (backendContentType === 'json') {
        if (bare === 'web_search' || bare === 'search')
            return 'search';
        return 'generic';
    }
    if (backendContentType === 'text') {
        if (bare === 'web_fetch' || bare === 'fetch')
            return 'web_fetch';
        return 'generic';
    }
    if (bare === 'web_search' || bare === 'search')
        return 'search';
    if (bare === 'web_fetch' || bare === 'fetch')
        return 'web_fetch';
    return 'generic';
}
let highlightCounter = 0;
export const usePanelStore = create((set) => ({
    view: { type: 'closed' },
    openTool: (toolName, toolArgs, toolResult, contentType, toolRef, highlightText) => set({
        view: {
            type: 'tool',
            toolName,
            toolArgs,
            toolResult,
            contentType: mapContentType(toolName, contentType),
            toolRef: toolRef ?? null,
            highlightText: highlightText ?? null,
            highlightKey: ++highlightCounter,
        },
    }),
    openArtifact: (conversationId, artifactId) => set({
        view: { type: 'artifact', conversationId, artifactId },
    }),
    openAttachment: (info) => set({
        view: { type: 'attachment', info },
    }),
    openBrowser: () => set({ view: { type: 'browser' } }),
    close: () => set({ view: { type: 'closed' } }),
}));
//# sourceMappingURL=panelStore.js.map