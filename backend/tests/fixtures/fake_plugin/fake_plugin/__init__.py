from cubeplex.plugins import CUBEPLEX_PLUGIN_API_VERSION, PluginManifest

MANIFEST = PluginManifest(
    api_version=CUBEPLEX_PLUGIN_API_VERSION,
    name="fake",
    version="0.0.1",
    description="Fixture plugin for Layer 1 contract tests.",
)
