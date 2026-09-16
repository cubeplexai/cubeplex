# K8s Deployment — Graceful Restart

The CubePlex backend closes HTTP and SSE transports, then drains in-flight
CubeLoop runs on `SIGTERM` before exiting. Transport shutdown has a short,
separate deadline so an idle permanent stream cannot block agent draining.

## Probes

- `GET /health/live` → liveness. Always 200 while the process is up.
- `GET /health/ready` → readiness. 503 while draining.

## Recommended deployment fragment

```yaml
spec:
  # Agent drain (3600s) + transport shutdown and final cleanup headroom.
  terminationGracePeriodSeconds: 3660
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
| `api.transport_shutdown_timeout_seconds` | 10 | Hard cap for active HTTP and SSE connections before lifespan shutdown starts. |
| `lifecycle.graceful_drain_timeout_seconds` | 3600 | Hard cap on drain wait before forced cancel. Match `terminationGracePeriodSeconds`. |
| `lifecycle.stale_run_threshold_seconds` | 180 | Seconds without an event before bootstrap declares a `running` run stale and clears its active-run lock. Above the 120s execute-tool cap so a still-running command is not declared dead on refresh. |

Allow extra orchestrator headroom beyond the configured run drain so transport
shutdown and database, Redis, connector, and tracing cleanup can finish.

## Force-killing a slow drain

For an unscheduled exit:

```
kubectl delete pod <pod> --grace-period=0 --force
```

This skips drain. In-flight runs die mid-stream and surface to clients as
stale runs the next time the user opens the conversation.
