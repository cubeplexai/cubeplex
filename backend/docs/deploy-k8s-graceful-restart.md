# K8s Deployment — Graceful Restart

The cubeplex backend drains in-flight LangGraph runs on `SIGTERM` before
exiting. To get zero-downtime rolling restarts, pair this with a long
termination grace period and the split health probes.

## Probes

- `GET /health/live` → liveness. Always 200 while the process is up.
- `GET /health/ready` → readiness. 503 while draining.

## Recommended deployment fragment

```yaml
spec:
  terminationGracePeriodSeconds: 3600   # match lifecycle.graceful_drain_timeout_seconds
  containers:
    - name: cubeplex
      readinessProbe:
        httpGet: { path: /health/ready, port: 8000 }
        periodSeconds: 5
      livenessProbe:
        httpGet: { path: /health/live, port: 8000 }
        periodSeconds: 30
```

## Tunables

`backend/config.yaml`:

| Key | Default | Notes |
|---|---|---|
| `lifecycle.graceful_drain_timeout_seconds` | 3600 | Hard cap on drain wait before forced cancel. Match `terminationGracePeriodSeconds`. |
| `lifecycle.stale_run_threshold_seconds` | 120 | Seconds without an event before bootstrap declares a `running` run stale and clears its active-run lock. |
| `lifecycle.dev_double_signal_force_exit` | true | Second `Ctrl-C` forces immediate exit. Set false in prod if you want to require an external SIGKILL. |

## Force-killing a slow drain

For an unscheduled exit:

```
kubectl delete pod <pod> --grace-period=0 --force
```

This skips drain. In-flight runs die mid-stream and surface to clients as
stale runs the next time the user opens the conversation.
