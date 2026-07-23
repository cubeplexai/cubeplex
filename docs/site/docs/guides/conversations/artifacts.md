---
sidebar_position: 3
title: Artifacts
---

# Artifacts

Artifacts are deliverables the agent produces during a conversation — files, websites, images, code, data, and more. Unlike plain-text responses, artifacts are versioned, previewable, and downloadable.

## How artifacts are created

You do not create artifacts directly. The agent creates them by calling one of two tools:

- **`save_artifact`** — registers a file or set of files the agent wrote (a website, document, code project, data file, or skill bundle) as an artifact.
- **`generate_image`** — produces an image artifact directly from a prompt.

This happens naturally when you ask the agent to build something concrete:

- "Build me a landing page"
- "Generate a bar chart from this CSV"
- "Write a Python script that processes these logs"
- "Create a project proposal document"
- "Draw an illustration of a mountain at sunset"

For `save_artifact`, the agent writes the files (using code execution in the sandbox), then registers them as an artifact so they appear in your conversation.

## Artifact types

| Type | What it is | Preview behavior |
|---|---|---|
| **Website** | HTML/CSS/JS sites or apps | Live rendered preview in an iframe |
| **Document** | Markdown, PDF, DOCX, or other text documents | Rendered document preview (Markdown rendered, PDF embedded, Office documents via viewer) |
| **Image** | PNG, SVG, JPEG, and similar | Inline image preview |
| **Code** | Source code files or projects | Syntax-highlighted code viewer |
| **Data** | CSV, JSON, Excel spreadsheets | Tabular data preview |
| **Skill** | A skill bundle with a SKILL.md entry point | Skill preview with option to publish to your workspace |
| **File** | Anything that does not fit the above categories | Download link with basic metadata |

## Viewing artifacts

When the agent creates an artifact, an **artifact card** appears inline in the chat message. The card shows:

- The artifact name and type icon.
- A brief description (if the agent provided one).
- A version badge (for artifacts with multiple versions).
- **Preview** (eye icon) and **Download** buttons.

Clicking the card (or the preview button) opens the **artifact panel** on the right side of the screen. The panel renders a live preview based on the artifact type — websites run in a sandboxed iframe, images display at full resolution, code gets syntax highlighting, and so on.

On desktop, use **Expand preview** in the panel header to open a large centered in-app view (most of the viewport). Press Esc, click the backdrop, or use **Exit expand** / the theater header **Close** control to return to the side panel without losing the selected artifact.

While expanded, the theater is a modal overlay — the side panel underneath is not interactive. Exit expand first, then use the side panel **Close** control if you want to dismiss the preview entirely. If you are interacting inside an embedded preview (website or Office document), Esc may stay inside that document; use **Exit expand** in the theater header instead.

![Artifact card with the right-side preview panel open](/img/conversations/artifact-panel.png)

### Markdown documents in chat

Markdown document artifacts (`.md` / `.markdown` / `.mdx`, or `text/markdown`)
render **inline** in the transcript — similar to image previews — so you can
read them without opening the side panel.

- **Edit** opens a rich-text editor (TipTap). Save creates a **new version** of
  the artifact. If the conversation sandbox and file path are still valid,
  CubePlex also tries to write the same content back to that path; if not, the
  save still succeeds and you get a non-blocking notice.
- **Select text** in the rendered body, then **Quote**, to insert the passage
  and artifact context into the composer so you can ask the agent to revise it.

### Artifact gallery

At the top of the chat area, a collapsible **Artifacts** bar shows all artifacts created in the current conversation. Click to expand the gallery and quickly jump to any artifact without scrolling through the message history.

## Versioning

The agent can update an artifact across multiple turns. When you say "make the header blue" or "add a footer to the page," the agent creates a **new version** of the existing artifact rather than a separate artifact.

Versioned artifacts show a version badge (e.g., "v3") on their card. In the artifact panel, click the version badge to open a dropdown listing all versions with timestamps. Select any version to preview it or download that specific version.

## Downloading artifacts

Every artifact can be downloaded to your local machine:

- Click the **download icon** on the artifact card in the chat.
- Or click **download** in the artifact panel header.

For multi-file artifacts (like a website with HTML, CSS, and JS files), the download packages everything into a single archive.

## Skill artifacts

When the agent creates a skill (via the skill-creator workflow), the artifact type is **skill**. Skill artifacts have a special action: **Add to workspace**. Clicking this publishes the skill to your workspace so it becomes available in future conversations (the agent can load it via `load_skill`). The version comes from the `version` field in the skill's `SKILL.md` frontmatter; to update a published skill, bump that version and add it again.

## Tips

- **Be specific about what you want.** "Build a dashboard" is vague. "Build a dashboard with three cards showing daily active users, revenue, and error rate using Chart.js" gives the agent enough detail to produce a useful artifact on the first try.
- **Iterate by describing changes.** After the agent creates an artifact, ask for modifications in follow-up messages. The agent updates the same artifact with a new version, keeping your history clean.
- **Use the preview panel.** Do not rely solely on the agent's text description. Open the preview to see exactly what was produced, especially for websites and images.
- **Download important artifacts.** Artifacts live within the conversation. If you need the output long-term, download it to your local machine or a shared drive.
