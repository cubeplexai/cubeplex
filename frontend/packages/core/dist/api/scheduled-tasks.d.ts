import type { ScheduledTaskCreate, ScheduledTaskOut, ScheduledTaskPatch, ScheduledTaskRunOut } from '../types/scheduled-task';
import { type ApiClient } from './client';
export declare function listScheduledTasks(client: ApiClient): Promise<ScheduledTaskOut[]>;
export declare function getScheduledTask(client: ApiClient, id: string): Promise<ScheduledTaskOut>;
export declare function createScheduledTask(client: ApiClient, body: ScheduledTaskCreate): Promise<ScheduledTaskOut>;
export declare function patchScheduledTask(client: ApiClient, id: string, body: ScheduledTaskPatch): Promise<ScheduledTaskOut>;
export declare function pauseScheduledTask(client: ApiClient, id: string): Promise<ScheduledTaskOut>;
export declare function resumeScheduledTask(client: ApiClient, id: string): Promise<ScheduledTaskOut>;
export declare function deleteScheduledTask(client: ApiClient, id: string): Promise<void>;
export declare function listScheduledTaskRuns(client: ApiClient, id: string): Promise<ScheduledTaskRunOut[]>;
//# sourceMappingURL=scheduled-tasks.d.ts.map