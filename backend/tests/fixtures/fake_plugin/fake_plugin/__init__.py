from cubebox.plugins import CUBEBOX_PLUGIN_API_VERSION, PluginManifest

MANIFEST = PluginManifest(
    api_version=CUBEBOX_PLUGIN_API_VERSION,
    name="fake",
    version="0.0.1",
    description="Fixture plugin for Layer 1 contract tests.",
)
