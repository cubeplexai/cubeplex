export function formatCostUsd(micro, currency = 'USD') {
    const amount = micro / 1000000;
    return `${currency} ${amount.toFixed(4)}`;
}
//# sourceMappingURL=billing.js.map