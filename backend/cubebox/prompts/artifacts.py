"""Artifact prompt — injected by ArtifactMiddleware."""

ARTIFACT_PROMPT = """## Artifacts

When you create a deliverable (document, website, app, visualization, data file, etc.), \
register it using the `save_artifact` tool so the user can preview and download it.

**Workflow:**
1. Write files using `execute` (shell commands, heredoc, python scripts, etc.)
2. Call `save_artifact` with the file/directory path and a descriptive name

**artifact_type guide:**
- "website" — HTML/CSS/JS sites or apps (set entry_file to the main HTML file)
- "document" — Markdown, text, or generated documents (PDF, DOCX, etc.)
- "image" — PNG, SVG, JPG images (e.g. matplotlib output)
- "code" — Source code files or projects
- "data" — CSV, JSON, Excel data files
- "file" — Anything else

**For updates:** Pass the existing artifact_id to update rather than create a new artifact.
"""
