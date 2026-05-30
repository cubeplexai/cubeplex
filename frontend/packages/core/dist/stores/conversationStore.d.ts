import type { Conversation } from '../types';
import type { ApiClient } from '../api';
export interface ConversationStore {
    conversations: Conversation[];
    activeId: string | null;
    isLoading: boolean;
    isFetchingList: boolean;
    error: string | null;
    /** ids currently mid-pin-request — UI uses this to disable the button. */
    pinPending: Record<string, true>;
    fetchList(client: ApiClient): Promise<void>;
    create(client: ApiClient, title?: string, opts?: {
        draft?: boolean;
    }): Promise<Conversation>;
    remove(client: ApiClient, id: string): Promise<void>;
    rename(client: ApiClient, id: string, title: string): Promise<void>;
    setPin(client: ApiClient, id: string, isPinned: boolean): Promise<void>;
    generateTitle(client: ApiClient, id: string, content: string): Promise<void>;
    setActive(id: string | null): void;
}
export declare const useConversationStore: import("zustand").UseBoundStore<import("zustand").StoreApi<ConversationStore>>;
//# sourceMappingURL=conversationStore.d.ts.map