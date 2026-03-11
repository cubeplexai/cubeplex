"""Skills loader for sandbox environments.

Loads skills from local filesystem and prepares them for syncing to sandbox containers.
"""

from pathlib import Path

from loguru import logger


class SkillLoader:
    """Load skills from local filesystem for syncing to sandbox."""

    def __init__(self, skills_root: Path) -> None:
        """Initialize skill loader.

        Args:
            skills_root: Root directory containing skill subdirectories
        """
        self.skills_root = skills_root

    def load_builtin(self) -> list[tuple[str, bytes]]:
        """Load all builtin skills from the skills root directory.

        Scans the skills_root directory for skill subdirectories. Each subdirectory
        is treated as a skill, and all files within are loaded recursively.

        Returns:
            List of (container_path, content) tuples ready for upload.
            Container paths are formatted as: /.skills/builtin/{skill_name}/{file_path}

        Example:
            Input: backend/skills/builtin/git-commit/SKILL.md
            Output: [("/.skills/builtin/git-commit/SKILL.md", b"...")]
        """
        if not self.skills_root.exists():
            logger.warning("Skills root directory does not exist: {}", self.skills_root)
            return []

        files: list[tuple[str, bytes]] = []

        # Iterate over each skill directory
        for skill_dir in self.skills_root.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name
            logger.debug("Loading skill: {}", skill_name)

            # Recursively load all files in the skill directory
            for file_path in skill_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                # Calculate relative path within the skill
                rel_path = file_path.relative_to(skill_dir)

                # Build container path: /.skills/builtin/{skill_name}/{rel_path}
                container_path = f"/.skills/builtin/{skill_name}/{rel_path}"

                # Read file content as bytes
                try:
                    content = file_path.read_bytes()
                    files.append((container_path, content))
                    logger.debug("Loaded skill file: {} ({} bytes)", container_path, len(content))
                except Exception as e:
                    logger.error("Failed to read skill file {}: {}", file_path, e)

        logger.info("Loaded {} files from {} skills", len(files), len(list(self.skills_root.iterdir())))
        return files
