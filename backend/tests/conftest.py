import os

# Set environment BEFORE any imports
os.environ["ENV_FOR_DYNACONF"] = "test"

from cubebox.config import config

if config.langsmith.enabled:
    print("setup langsmith")
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = config.langsmith.key
