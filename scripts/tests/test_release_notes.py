"""Contracts for publishing the documentation changelog on GitHub."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


def load_renderer() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "release-notes.py"
    spec = importlib.util.spec_from_file_location("release_notes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.english = self.root / "docs/site/docs/changelog/0.9.0.md"
        self.chinese = self.root / (
            "docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/changelog/0.9.0.md"
        )
        for path in (self.english, self.chinese):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('---\ntitle: "0.9.0"\n---\n\n# CubePlex 0.9.0\n')
        self.renderer = load_renderer()

    def test_publishable_body_preserves_content_and_resolves_doc_links(self) -> None:
        self.english.write_text(
            '---\ntitle: "0.9.0"\n---\n\n# CubePlex 0.9.0\n\n'
            "Feature details.\n\n[Upgrade](../deployment/docker-compose.md#maintenance)\n"
            "[GitHub](https://github.com/cubeplexai/cubeplex)\n"
            "```bash\n[example](../example.md)\n```\n"
        )
        result = self.renderer.render_release_notes(self.root, "v0.9.0")
        self.assertTrue(result.startswith("# CubePlex 0.9.0\n\nFeature details."))
        self.assertNotIn('title: "0.9.0"', result)
        self.assertIn(
            "[Upgrade](https://cubeplex.ai/docs/deployment/docker-compose#maintenance)", result
        )
        self.assertIn("[GitHub](https://github.com/cubeplexai/cubeplex)", result)
        self.assertIn("```bash\n[example](../example.md)\n```", result)
        self.assertIn("https://cubeplex.ai/docs/zh-Hans/changelog/0.9.0", result)

    def test_missing_translation_blocks_publication(self) -> None:
        self.chinese.unlink()
        with self.assertRaises(FileNotFoundError):
            self.renderer.render_release_notes(self.root, "v0.9.0")

    def test_empty_or_invalid_changelog_blocks_publication(self) -> None:
        for content in ('---\ntitle: "0.9.0"\n---\n', "No frontmatter", "---\ntitle: 0.9.0"):
            with self.subTest(content=content):
                self.english.write_text(content)
                with self.assertRaises(ValueError):
                    self.renderer.render_release_notes(self.root, "v0.9.0")

    def test_invalid_tag_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.renderer.render_release_notes(self.root, "../0.9.0")


if __name__ == "__main__":
    unittest.main()
