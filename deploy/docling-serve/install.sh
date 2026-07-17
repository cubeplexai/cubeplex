#!/usr/bin/env bash

set -euo pipefail

base_dir="${1:-/opt/docling-serve}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p \
  "${base_dir}/compose" \
  "${base_dir}/models" \
  "${base_dir}/cpu/cache" \
  "${base_dir}/cu130/cache"

install -m 0644 \
  "${script_dir}/docker-compose.yml" \
  "${base_dir}/compose/docker-compose.yml"

cat <<EOF
Prepared directories under ${base_dir}:
  - ${base_dir}/compose
  - ${base_dir}/models
  - ${base_dir}/cpu/cache
  - ${base_dir}/cu130/cache

Compose file:
  ${base_dir}/compose/docker-compose.yml

Official pull:
  cd ${base_dir}/compose
  docker compose pull

Fallback to quay if GHCR is slow or blocked:
  export DOCLING_REGISTRY=quay.io/docling-project
  docker compose pull

Mainland China mirror fallback (third-party sync, verify before production use):
  export DOCLING_REGISTRY=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/docling-project
  docker compose pull

Mainland China HuggingFace mirror for model downloads (docling-models job):
  export HF_ENDPOINT=https://hf-mirror.com
  # gated/private repos also need: export HF_TOKEN=hf_xxx

Start services:
  docker compose up -d --profile gpu
  or
  docker compose up -d --profile cpu

Check progress / status:
  docker compose ps
  docker compose logs -f --tail=100
  docker image ls | grep docling-serve
EOF
