export declare function useDeploymentMode(): {
    mode: "single_tenant" | "multi_tenant" | undefined;
    needsOrgSetup: boolean;
    version: string | undefined;
    sandboxEnabled: boolean;
    loading: boolean;
    error: Error | undefined;
};
//# sourceMappingURL=useDeploymentMode.d.ts.map