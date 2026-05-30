import { create } from 'zustand';
import { deleteAttachment, listAttachments, uploadAttachment } from '../api/attachments';
const newTempId = () => `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const abortControllers = {};
export const useAttachmentStore = create((set, get) => ({
    staging: {},
    async upload(client, convId, files) {
        const next = files.map((f) => ({
            tempId: newTempId(),
            filename: f.name,
            size: f.size,
            progress: 0,
            status: 'uploading',
        }));
        for (const item of next)
            abortControllers[item.tempId] = new AbortController();
        set((s) => ({
            staging: {
                ...s.staging,
                [convId]: [...(s.staging[convId] || []), ...next],
            },
        }));
        await Promise.all(next.map(async (item, idx) => {
            const controller = abortControllers[item.tempId];
            try {
                const dto = await uploadAttachment(client, convId, files[idx], (p) => {
                    set((s) => {
                        const list = (s.staging[convId] || []).map((u) => u.tempId === item.tempId ? { ...u, progress: p } : u);
                        return { staging: { ...s.staging, [convId]: list } };
                    });
                }, controller?.signal);
                set((s) => {
                    const list = (s.staging[convId] || []).map((u) => u.tempId === item.tempId
                        ? { ...u, progress: 1, status: 'done', serverFile: dto }
                        : u);
                    return { staging: { ...s.staging, [convId]: list } };
                });
            }
            catch (err) {
                const aborted = err?.name === 'AbortError';
                if (aborted) {
                    set((s) => {
                        const list = (s.staging[convId] || []).filter((u) => u.tempId !== item.tempId);
                        return { staging: { ...s.staging, [convId]: list } };
                    });
                }
                else {
                    const uploadError = err;
                    set((s) => {
                        const list = (s.staging[convId] || []).map((u) => u.tempId === item.tempId
                            ? {
                                ...u,
                                status: 'error',
                                error: uploadError.message || String(err),
                                errorCode: uploadError.errorCode,
                            }
                            : u);
                        return { staging: { ...s.staging, [convId]: list } };
                    });
                }
            }
            finally {
                delete abortControllers[item.tempId];
            }
        }));
    },
    async cancel(convId, tempId) {
        const controller = abortControllers[tempId];
        if (controller) {
            controller.abort();
            delete abortControllers[tempId];
        }
        set((s) => {
            const list = (s.staging[convId] || []).filter((u) => u.tempId !== tempId);
            return { staging: { ...s.staging, [convId]: list } };
        });
    },
    async remove(client, convId, tempId) {
        const item = (get().staging[convId] || []).find((u) => u.tempId === tempId);
        if (item?.serverFile) {
            try {
                await deleteAttachment(client, convId, item.serverFile.id);
            }
            catch {
                // best-effort — orphan reaper will clean it up server-side
            }
        }
        set((s) => {
            const list = (s.staging[convId] || []).filter((u) => u.tempId !== tempId);
            return { staging: { ...s.staging, [convId]: list } };
        });
    },
    clear(convId) {
        set((s) => {
            const next = { ...s.staging };
            delete next[convId];
            return { staging: next };
        });
    },
    attachedIds(convId) {
        return (get().staging[convId] || [])
            .filter((u) => u.status === 'done' && u.serverFile)
            .map((u) => u.serverFile.id);
    },
    async hydrate(client, convId) {
        let list;
        try {
            list = await listAttachments(client, convId, 'pending');
        }
        catch {
            return;
        }
        if (!list.attachments.length)
            return;
        set((s) => ({
            staging: {
                ...s.staging,
                [convId]: list.attachments.map((a) => ({
                    tempId: newTempId(),
                    filename: a.filename,
                    size: a.size_bytes,
                    progress: 1,
                    status: 'done',
                    serverFile: a,
                })),
            },
        }));
    },
}));
//# sourceMappingURL=attachmentStore.js.map