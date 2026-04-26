"""Health probes split for k8s.

- ``/health/live`` is the liveness probe. Always 200 while the process is up.
  Must not flip during drain or k8s will kill the pod before drain completes.
- ``/health/ready`` is the readiness probe. 503 while draining so the load
  balancer stops sending new traffic to this pod.
"""

from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request, response: Response) -> dict[str, str]:
    drain_state = getattr(request.app.state, "drain_state", None)
    if drain_state is not None and drain_state.is_draining():
        response.status_code = 503
        return {"status": "draining"}
    return {"status": "ok"}
