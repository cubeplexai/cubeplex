"""Root conftest: put deploy/kubernetes/egress-bundle and deploy/kubernetes/egress-bundle/addon on sys.path.

- `import webhook.*` resolves via the egress-bundle root (webhook is a package).
- `import inject` resolves via the addon/ dir (inject.py is a top-level module there,
  loaded directly by mitmproxy in production, so it cannot be a sub-package).
"""
import sys
import pathlib

_root = pathlib.Path(__file__).parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "addon"))
