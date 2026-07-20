# cubeplex on Kubernetes — Install Guide

A single `helm upgrade --install` deploys cubeplex (backend + frontend +
Postgres + Redis + rustfs, optionally the alibaba OpenSandbox umbrella) to
an existing Kubernetes cluster.

**The full, maintained install guide lives on the docs site:**
[cubeplex.ai/docs/deployment/kubernetes](https://cubeplex.ai/docs/deployment/kubernetes)
(中文版: [cubeplex.ai/docs/zh-Hans/deployment/kubernetes](https://cubeplex.ai/docs/zh-Hans/deployment/kubernetes))

It covers prerequisites, architecture, building and pushing images,
authoring `values.local.yaml` field by field, installing, post-install
verification, troubleshooting, and the full values reference.

This directory holds the chart, scripts, and templates the guide walks
through: `charts/cubeplex/`, `scripts/{build-and-push,helm-install,
smoke-test,e2e}.sh`. See [README.md](README.md) for the short quickstart.
Chart design notes:
[docs/dev/specs/2026-06-10-helm-deploy-design.md](../../docs/dev/specs/2026-06-10-helm-deploy-design.md).
