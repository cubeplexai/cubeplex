import type { ApiClient } from './client';
import type { AttachmentDto, AttachmentListDto, AttachmentStatus } from '../types/attachment';
export interface UploadAttachmentError extends Error {
    errorCode?: string;
}
export declare function uploadAttachment(client: ApiClient, conversationId: string, file: File, onProgress?: (fraction: number) => void, signal?: AbortSignal): Promise<AttachmentDto>;
export declare function listAttachments(client: ApiClient, conversationId: string, status?: AttachmentStatus | 'all'): Promise<AttachmentListDto>;
export declare function deleteAttachment(client: ApiClient, conversationId: string, attachmentId: string): Promise<void>;
//# sourceMappingURL=attachments.d.ts.map