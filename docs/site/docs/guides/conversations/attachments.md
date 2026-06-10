---
sidebar_position: 2
title: Attachments
---

# Attachments

You can attach files to your messages so the agent can read, analyze, or reference them. Attachments are scoped to the conversation where they are uploaded.

## Supported file types

CubeBox classifies attachments into three kinds:

| Kind | Examples |
|---|---|
| **Image** | PNG, JPEG, WebP, GIF |
| **Document** | PDF, plain text, Markdown, CSV, DOCX, XLSX, JSON, YAML |
| **Other** | Any other file type allowed by your organization's configuration |

The exact set of allowed MIME types is configured by your org admin. If you try to upload a file type that is not allowed, the upload is rejected with an error.

## How to attach files

### Click to upload

1. Click the **paperclip icon** in the input bar.
2. Select one or more files from the file picker.
3. The files appear as chips above the input area while they upload.
4. Type your message (optional) and press **Enter** to send.

You can attach multiple files to a single message.

### Drag and drop

Drag files from your desktop onto the chat area. A drop zone overlay appears confirming the drop target. Release the files and they begin uploading immediately.

### Remove before sending

While files are still uploading or staged (before you send the message), click the **X** on any file chip to remove it. Once a message is sent, its attachments become permanent and cannot be removed.

## How attachments reach the agent

When you send a message with attachments, the files are:

1. **Stored** in CubeBox's object store.
2. **Injected** into the agent's sandbox at a known path so it can access them with code execution tools.
3. **Referenced** in the message context so the agent knows what files are available and their types.

For images, the agent can also "see" the image directly through vision-capable models.

## Viewing attachments in the chat

Sent attachments appear above the message bubble:

- **Images** display as thumbnails. Click a thumbnail to open a full-size lightbox.
- **Documents and other files** display as compact chips showing the filename and size. Click to download or preview.

## Size and quota limits

| Limit | Default |
|---|---|
| **Max file size** | 50 MB per file |
| **Max per conversation** | 500 MB total across all attachments |

These limits are configurable by the org admin. If you exceed the per-file limit, the upload is rejected. If you exceed the per-conversation quota, you need to start a new conversation or ask an admin to adjust the limit.

## Tips

- **Attach context documents early.** Upload reference material (specs, data files, style guides) in your first message so the agent has full context from the start.
- **Use images with vision-capable models.** If you need the agent to interpret screenshots, diagrams, or photos, make sure the selected model supports vision. See [Model Selection](./model-selection.md).
- **Combine text and files.** Always include a text message explaining what you want the agent to do with the attached files. A bare file upload with no instructions produces less useful results.
- **Check file size before uploading.** Large files (especially images) can slow down the upload. If you are sharing code or data, consider zipping multiple files into a single archive.
