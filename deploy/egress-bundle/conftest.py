"""Root conftest: put deploy/egress-bundle on sys.path so `import webhook.*` works."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
