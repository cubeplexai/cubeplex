import type { MemoryItem, MemoryScope, MemoryStatus, MemoryType } from '../types/memory';
import { type ApiClient } from './client';
export interface ListMemoryOptions {
    scope?: MemoryScope;
    type?: MemoryType;
    status?: MemoryStatus;
    q?: string;
}
export interface CreateMemoryBody {
    scope: MemoryScope;
    type: MemoryType;
    content: string;
    confidence?: number;
}
export interface UpdateMemoryBody {
    content?: string;
    type?: MemoryType;
    confidence?: number;
    status?: MemoryStatus;
}
export declare function listMemory(client: ApiClient, opts?: ListMemoryOptions): Promise<MemoryItem[]>;
export declare function createMemory(client: ApiClient, body: CreateMemoryBody): Promise<MemoryItem>;
export declare function updateMemory(client: ApiClient, id: string, body: UpdateMemoryBody): Promise<MemoryItem>;
export declare function archiveMemory(client: ApiClient, id: string): Promise<void>;
//# sourceMappingURL=memory.d.ts.map