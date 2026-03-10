"""
cubebox Configuration Management

Uses dynaconf for environment-based configuration with support for:
- YAML configuration files
- Environment variable overrides
- Development/production/testing environments
"""

import os
from pathlib import Path

import dynaconf

# Get the backend directory (where config files are located)
backend_dir = Path(__file__).parent.parent

# Load configuration based on environment
env = os.getenv("ENV_FOR_DYNACONF", "development")
settings_files = [
    str(backend_dir / "config.yaml"),  # Base configuration
    str(backend_dir / f"config.{env}.yaml"),
    str(backend_dir / f"config.{env}.local.yaml"),
]

config = dynaconf.Dynaconf(
    environments=True,
    dotenv_path=str(backend_dir / ".env"),
    envvar_prefix="CUBEBOX",
    settings_files=settings_files,
    load_dotenv=True,
)

if config.langsmith.enabled:
    print("init langsmith")
    print(config.langsmith.key)
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = config.langsmith.key
