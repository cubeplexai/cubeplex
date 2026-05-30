// frontend/packages/core/src/stores/toolDetailStore.ts
// Thin compatibility layer — delegates to the unified panelStore.
import { usePanelStore } from './panelStore';
export const useToolDetailStore = Object.assign(function useToolDetailStoreHook(selector) {
    return usePanelStore((panel) => {
        const v = panel.view;
        const facade = v.type === 'tool'
            ? {
                isOpen: true,
                toolName: v.toolName,
                toolArgs: v.toolArgs,
                toolResult: v.toolResult,
                contentType: v.contentType,
                toolRef: v.toolRef,
                highlightText: v.highlightText,
                highlightKey: v.highlightKey,
                open: panel.openTool,
                close: panel.close,
            }
            : {
                isOpen: false,
                toolName: '',
                toolArgs: {},
                toolResult: null,
                contentType: 'generic',
                toolRef: null,
                highlightText: null,
                highlightKey: 0,
                open: panel.openTool,
                close: panel.close,
            };
        return selector(facade);
    });
}, {
    getState() {
        const panel = usePanelStore.getState();
        const v = panel.view;
        if (v.type === 'tool') {
            return {
                isOpen: true,
                toolName: v.toolName,
                toolArgs: v.toolArgs,
                toolResult: v.toolResult,
                contentType: v.contentType,
                toolRef: v.toolRef,
                highlightText: v.highlightText,
                highlightKey: v.highlightKey,
                open: panel.openTool,
                close: panel.close,
            };
        }
        return {
            isOpen: false,
            toolName: '',
            toolArgs: {},
            toolResult: null,
            contentType: 'generic',
            toolRef: null,
            highlightText: null,
            highlightKey: 0,
            open: panel.openTool,
            close: panel.close,
        };
    },
});
//# sourceMappingURL=toolDetailStore.js.map