import type { CostAggregateRow, CostSummaryResponse, TimeseriesResponse } from '../types/billing';
import { type ApiClient } from './client';
export declare function fetchCostSummary(client: ApiClient, params?: {
    from?: string;
    to?: string;
}): Promise<CostSummaryResponse>;
export declare function fetchWorkspaceCost(client: ApiClient, wsId: string, params?: {
    from?: string;
    to?: string;
    group_by?: string;
}): Promise<CostAggregateRow[]>;
export declare function buildExportUrl(wsId?: string, params?: {
    from?: string;
    to?: string;
}): string;
export interface TimeseriesParams {
    dimension: 'workspace' | 'model' | 'user';
    granularity?: 'day' | 'week';
    from?: string;
    to?: string;
    workspace_ids?: string[];
    models?: string[];
}
export declare function fetchCostTimeseries(client: ApiClient, params: TimeseriesParams): Promise<TimeseriesResponse>;
//# sourceMappingURL=billing.d.ts.map