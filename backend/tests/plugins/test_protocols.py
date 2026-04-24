from cubebox.plugins.protocols import CUBEBOX_PLUGIN_API_VERSION, PluginManifest


def test_plugin_manifest_constructs_with_required_fields() -> None:
    m = PluginManifest(api_version=1, name="test-plugin", version="0.1.0")
    assert m.api_version == 1
    assert m.name == "test-plugin"
    assert m.version == "0.1.0"
    assert m.description == ""


def test_plugin_manifest_accepts_description() -> None:
    m = PluginManifest(api_version=1, name="x", version="0.1.0", description="Hello")
    assert m.description == "Hello"


def test_api_version_constant_is_int_one() -> None:
    assert CUBEBOX_PLUGIN_API_VERSION == 1
    assert isinstance(CUBEBOX_PLUGIN_API_VERSION, int)
