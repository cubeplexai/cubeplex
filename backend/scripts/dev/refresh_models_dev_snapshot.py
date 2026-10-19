"""Refresh the committed models.dev provider snapshot.

The snapshot is rewritten only when the reduced provider data changes, so a
daily run does not create an empty commit just because the clock moved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.request import Request, urlopen

from cubeplex.llm.models_dev import reduce_provider_catalog, snapshot_path, snapshot_source

_COPYRIGHT = "Copyright (c) 2025 models.dev"


def main() -> None:
    request = Request(snapshot_source(), headers={"User-Agent": "cubeplex-models-dev-snapshot"})
    with urlopen(request, timeout=120) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise SystemExit("models.dev api.json is not an object")
    providers = reduce_provider_catalog(payload)
    path = snapshot_path()
    previous: dict[str, object] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            previous = loaded
    if previous.get("providers") == providers:
        print(f"unchanged {path}")
        return
    document = {
        "source": snapshot_source(),
        "retrieved_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "license": "MIT",
        "copyright": _COPYRIGHT,
        "providers": providers,
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path} providers={len(providers)}")


if __name__ == "__main__":
    main()
