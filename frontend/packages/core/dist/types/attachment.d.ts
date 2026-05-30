export type AttachmentKind = 'image' | 'document' | 'other';
export type AttachmentStatus = 'pending' | 'attached';
export interface AttachmentDto {
    id: string;
    filename: string;
    kind: AttachmentKind;
    mime_type: string;
    size_bytes: number;
    width: number | null;
    height: number | null;
    status: AttachmentStatus;
    thumbnail_url: string | null;
    download_url: string;
    created_at: string;
}
export interface AttachmentListDto {
    attachments: AttachmentDto[];
    total: number;
}
//# sourceMappingURL=attachment.d.ts.map