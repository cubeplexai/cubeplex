import type { ApiClient } from '../api/client';
import type { AttachmentDto } from '../types/attachment';
export interface UploadingFile {
    tempId: string;
    filename: string;
    size: number;
    progress: number;
    status: 'uploading' | 'done' | 'error';
    serverFile?: AttachmentDto;
    error?: string;
    errorCode?: string;
}
interface AttachmentStoreState {
    staging: Record<string, UploadingFile[]>;
    upload(client: ApiClient, convId: string, files: File[]): Promise<void>;
    cancel(convId: string, tempId: string): Promise<void>;
    remove(client: ApiClient, convId: string, tempId: string): Promise<void>;
    clear(convId: string): void;
    attachedIds(convId: string): string[];
    hydrate(client: ApiClient, convId: string): Promise<void>;
}
export declare const useAttachmentStore: import("zustand").UseBoundStore<import("zustand").StoreApi<AttachmentStoreState>>;
export {};
//# sourceMappingURL=attachmentStore.d.ts.map