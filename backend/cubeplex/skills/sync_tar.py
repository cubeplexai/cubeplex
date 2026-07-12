"""tar.gz packing on the backend side + shell-command building for the
extract+cleanup step inside the sandbox."""

from __future__ import annotations

import io
import shlex
import tarfile

# Shared constant so lazy.py (upload), sync_tar.py (extract cmd), and the
# MemSandbox test stub all reference the same path — a mismatch would silently
# no-op the cold-start extract.
SKILLS_DELTA_TGZ_PATH = "/tmp/skills_delta.tgz"


def build_tarball(files: list[tuple[str, bytes]]) -> bytes:
    """Pack ``files`` into a gzip'd tar blob.

    Paths are stored relative (no leading slash) so the sandbox-side extract
    can ``tar -xzf ... -C <skills_root>``. ``compresslevel=1`` keeps CPU low —
    skill bundles are mostly small text where light compression already pays.
    ``mtime=0`` keeps output deterministic for tests.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tf:
        for rel_path, body in files:
            normalised = rel_path.lstrip("/")
            info = tarfile.TarInfo(name=normalised)
            info.size = len(body)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def build_extract_and_remove_cmd(
    *,
    skills_root: str,
    has_push: bool,
    to_repush_names: list[str],
    to_remove: list[str],
) -> str:
    """Build a single shell command chain that:
      1. mkdir -p skills_root
      2. rm -rf each /skills_root/<name> in to_repush_names (wipes any
         leftover old-version dirs before extract — F9)
      3. extracts /tmp/skills_delta.tgz into skills_root (only if has_push)
      4. removes any sub-dirs listed in to_remove

    Order matters: repush-wipe BEFORE extract, otherwise we'd delete what we
    just put down.

    Returns empty string when there's nothing to do.

    Paths are ``shlex.quote``-wrapped so spaces / Unicode / quotes can't break
    out of the command.
    """
    segments: list[str] = []
    quoted_root = shlex.quote(skills_root)
    if has_push:
        segments.append(f"mkdir -p {quoted_root}")
        for name in to_repush_names:
            target = shlex.quote(f"{skills_root}/{name}")
            segments.append(f"rm -rf {target}")
        segments.append(f"tar -xzf {SKILLS_DELTA_TGZ_PATH} -C {quoted_root}")
        segments.append(f"rm -f {SKILLS_DELTA_TGZ_PATH}")
    for name in to_remove:
        target = shlex.quote(f"{skills_root}/{name}")
        segments.append(f"rm -rf {target}")
    return " && ".join(segments)
