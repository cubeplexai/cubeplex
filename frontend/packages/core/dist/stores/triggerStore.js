import { create } from 'zustand';
import { listTriggers, createTrigger, updateTrigger, deleteTrigger, rotateSecret as apiRotateSecret, listTriggerEvents, replayEvent as apiReplayEvent, } from '../api/triggers';
export const useTriggerStore = create((set, get) => ({
    triggers: [],
    loading: false,
    selectedId: null,
    eventsByTrigger: {},
    eventsLoading: false,
    async load(client, wsId) {
        set({ loading: true });
        try {
            const triggers = await listTriggers(client, wsId);
            set({ triggers });
        }
        finally {
            set({ loading: false });
        }
    },
    async create(client, wsId, body) {
        const trigger = await createTrigger(client, wsId, body);
        set((s) => ({ triggers: [trigger, ...s.triggers] }));
        return trigger;
    },
    async update(client, wsId, id, patch) {
        const trigger = await updateTrigger(client, wsId, id, patch);
        set((s) => ({
            triggers: s.triggers.map((t) => (t.id === id ? trigger : t)),
        }));
        return trigger;
    },
    async remove(client, wsId, id) {
        await deleteTrigger(client, wsId, id);
        set((s) => ({
            triggers: s.triggers.filter((t) => t.id !== id),
            eventsByTrigger: Object.fromEntries(Object.entries(s.eventsByTrigger).filter(([k]) => k !== id)),
        }));
    },
    async rotate(client, wsId, id, body) {
        const result = await apiRotateSecret(client, wsId, id, body);
        // Refresh the trigger row so rotation fields are up-to-date
        await get().load(client, wsId);
        return result;
    },
    async loadEvents(client, wsId, id, query) {
        set({ eventsLoading: true });
        try {
            const events = await listTriggerEvents(client, wsId, id, query);
            set((s) => ({ eventsByTrigger: { ...s.eventsByTrigger, [id]: events } }));
        }
        finally {
            set({ eventsLoading: false });
        }
    },
    async replay(client, wsId, id, eventId) {
        await apiReplayEvent(client, wsId, id, eventId);
        await get().loadEvents(client, wsId, id);
    },
    reset() {
        set({
            triggers: [],
            loading: false,
            selectedId: null,
            eventsByTrigger: {},
            eventsLoading: false,
        });
    },
}));
//# sourceMappingURL=triggerStore.js.map