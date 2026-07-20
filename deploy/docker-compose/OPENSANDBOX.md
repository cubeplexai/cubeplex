# OpenSandbox under docker-compose

This content has moved into the main Docker Compose install guide on the
docs site, as an "Optional: sandbox execution (OpenSandbox)" section:

[cubeplex.ai/docs/deployment/docker-compose#optional-sandbox-execution-opensandbox](https://cubeplex.ai/docs/deployment/docker-compose#optional-sandbox-execution-opensandbox)

It covers what the `compose.opensandbox.yaml` overlay deploys, the
quickstart config steps, the compatibility matrix of what alibaba's
[OpenSandbox](https://github.com/alibaba/OpenSandbox) can and cannot do
under Docker runtime mode, verifying the overlay, and tearing it down.

The overlay file itself is [`compose.opensandbox.yaml`](compose.opensandbox.yaml)
and its config template is
[`config/opensandbox.toml.example`](config/opensandbox.toml.example).
