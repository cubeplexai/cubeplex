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

**Updating artifacts (IMPORTANT):**
- When you modify, improve, or recreate something that serves the same purpose as an \
existing artifact, you MUST pass the existing `artifact_id` to create a new version \
instead of a new artifact.
- This applies even if the file path or filename changes (e.g. rewriting `snake.html` \
as `snake-v2.html` is still the same artifact).
- Check the existing artifacts list below to find the matching artifact_id.
- Only create a new artifact (omit artifact_id) when the deliverable is genuinely new \
and unrelated to any existing artifact.
"""
