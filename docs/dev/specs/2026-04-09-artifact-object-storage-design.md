# Artifact Object Storage & Version History

## Problem

Artifacts are currently stored only in the sandbox filesystem, which is ephemeral (~10min TTL). This means:
- Previewing artifacts requires a running sandbox
- Version numbers increment but old content is lost — no history
- Artifacts become inaccessible after sandbox cleanup

## Solution

Upload artifact files to S3-compatible object storage (S3/OSS) during `save_artifact` execution. Serve previews from object storage instead of sandbox. Track version history in a new DB table and expose it in the UI via a clickable version badge popover.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Key structure | `artifacts/{conv_id}/{artifact_id}/v{version}/{path}` | Groups by conversation for easy cleanup |
| Preview method | Backend proxies from object storage | Same API contract, no CORS issues |
| Version history UI | Clickable version badge popover | Minimal UI change, badge already exists |
| Upload timing | Synchronous in `save_artifact` | Reliable — know upload succeeded before responding |
| S3 client | `aioboto3` | Async, fits the fully-async codebase |

## Object Storage

### Key Structure

```
artifacts/{conversation_id}/{artifact_id}/v{version}/{file_path}
```

Examples:
- Single file: `artifacts/conv123/art456/v1/report.pdf`
- Directory: `artifacts/conv123/art456/v2/index.html`, `artifacts/conv123/art456/v2/styles.css`

### Configuration

New section in `config.yaml`:

```yaml
objectstore:
  provider: "oss"        # "oss" or "s3"
  endpoint: "https://oss-cn-zhangjiakou.aliyuncs.com"
  bucket: "cubeplex-dev"
  region: "cn-zhangjiakou"
  access_key: ""         # via CUBEPLEX_OBJECTSTORE__ACCESS_KEY
  access_secret: ""      # via CUBEPLEX_OBJECTSTORE__ACCESS_SECRET
```

OSS uses S3 protocol but requires `path` addressing style. The client adapts based on `provider`.

## Backend Changes

### New Module: `cubeplex/objectstore/`

`client.py` — async S3/OSS client wrapper:

- `ObjectStoreClient` class initialized from config
- `upload_file(key, data, content_type)` — upload single file
- `upload_directory(prefix, sandbox, path)` — walk sandbox directory, upload all files
- `download_file(key) -> (bytes, content_type)` — download single file
- `list_objects(prefix) -> list[str]` — list keys under prefix

Provider differences handled internally:
- OSS: `addressing_style: path`, endpoint includes region
- S3: default virtual-hosted addressing

### New Model: `ArtifactVersion`

Table `artifact_versions`:

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `artifact_id` | UUID | FK to `artifacts.id` |
| `version` | int | Version number |
| `name` | str | Artifact name at this version |
| `description` | str or null | Description at this version |
| `path` | str | Original sandbox path |
| `entry_file` | str or null | Entry file for websites |
| `mime_type` | str or null | MIME type |
| `created_at` | datetime | When this version was created |

Index on `artifact_id` for fast version listing.

### Modified: `cubeplex/middleware/artifacts.py`

After validating path exists in sandbox:

1. Determine object storage prefix: `artifacts/{conv_id}/{artifact_id}/v{version}/`
2. Check if path is a file or directory in sandbox
3. For files: read from sandbox, upload to object storage
4. For directories: list files recursively in sandbox, upload each
5. Proceed with DB create/update as before
6. Create `ArtifactVersion` record

### Modified: `cubeplex/api/routes/v1/artifacts.py`

**Preview endpoint** — fetch from object storage instead of sandbox:
- Resolve key: `artifacts/{conv_id}/{art_id}/v{version}/{file_path}`
- Default to latest version; accept `?version=N` query param
- Download from object storage, return with correct Content-Type

**Download endpoint** — same change, fetch from object storage.

**New endpoint**: `GET /conversations/{conv_id}/artifacts/{artifact_id}/versions`
- Returns list of `{version, name, description, created_at}` ordered by version desc
- Used by the version popover in the frontend

### Modified: `cubeplex/repositories/artifact.py`

- Add `ArtifactVersionRepository` with `create()` and `list_by_artifact()` methods
- Existing `ArtifactRepository.update()` continues to bump version on the `artifacts` table

## Frontend Changes

### Modified: API Client

- Add `listArtifactVersions(client, conversationId, artifactId)` — calls new versions endpoint
- Preview/download functions accept optional `version` parameter, appended as `?version=N`

### Modified: Artifact Store

- Add `versions[artifactId]: ArtifactVersion[]` — cached version list per artifact
- Add `selectedVersion[artifactId]: number | null` — selected version for preview (null = latest)
- Add `loadVersions(conversationId, artifactId)` — fetch and cache version list
- Add `selectVersion(artifactId, version)` — set selected version

### Modified: `ArtifactPanel.tsx`

- Version badge (`v2`, `v3`, etc.) becomes clickable when `version > 1`
- Clicking opens a popover with version list:
  - Each row shows version number, name (if changed), and relative timestamp
  - Current version highlighted
  - Clicking a version updates `selectedVersion` in store, re-renders preview
- Preview components receive version prop, append `?version=N` to preview URL

## Preview Flow

```
User clicks artifact
  -> ArtifactPanel opens (latest version by default)
  -> Frontend requests GET /preview/{file_path}?version=N
    -> Backend builds key: artifacts/{conv_id}/{art_id}/v{N}/{file_path}
    -> Downloads from object storage
    -> Returns bytes with Content-Type header
  -> Frontend renders in appropriate preview component (unchanged logic)

User clicks version badge
  -> Popover shows version list (fetched once, cached)
  -> User selects older version
  -> Preview re-fetches with ?version=M
  -> Same rendering pipeline
```

## What Doesn't Change

- `save_artifact` tool interface (agent sees no difference)
- Artifact SSE event format
- ArtifactCard in chat messages
- Preview component selection logic (HTML/Code/Image/PDF/etc.)
- Artifact type system

## Migration

- New Alembic migration creates `artifact_versions` table
- Existing artifacts in DB have no stored files in object storage — their preview will fall back to sandbox if available, or show unavailable state
