"""Bridge functions callable from artifact HTML.

Importing this package registers every function. To add a capability, add a
module here that calls ``register_function`` at import time and list it below.
"""

from cubeplex.services.artifact_bridge.functions import chat  # noqa: F401
