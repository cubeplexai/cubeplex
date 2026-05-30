import type { ApiClient } from '../api/client';
import { type Trigger, type TriggerEvent, type CreateTriggerBody, type UpdateTriggerBody, type RotateSecretBody, type RotateSecretResult, type ListTriggerEventsQuery } from '../api/triggers';
export interface TriggerStore {
    triggers: Trigger[];
    loading: boolean;
    selectedId: string | null;
    eventsByTrigger: Record<string, TriggerEvent[]>;
    eventsLoading: boolean;
    load(client: ApiClient, wsId: string): Promise<void>;
    create(client: ApiClient, wsId: string, body: CreateTriggerBody): Promise<Trigger>;
    update(client: ApiClient, wsId: string, id: string, patch: UpdateTriggerBody): Promise<Trigger>;
    remove(client: ApiClient, wsId: string, id: string): Promise<void>;
    rotate(client: ApiClient, wsId: string, id: string, body: RotateSecretBody): Promise<RotateSecretResult>;
    loadEvents(client: ApiClient, wsId: string, id: string, query?: ListTriggerEventsQuery): Promise<void>;
    replay(client: ApiClient, wsId: string, id: string, eventId: string): Promise<void>;
    reset(): void;
}
export declare const useTriggerStore: import("zustand").UseBoundStore<import("zustand").StoreApi<TriggerStore>>;
//# sourceMappingURL=triggerStore.d.ts.map