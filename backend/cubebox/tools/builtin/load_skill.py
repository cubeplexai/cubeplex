"""Load Skill Tool

Provides direct access to skill content for the agent.
Reads SKILL.md files from the skills directory and returns their content.
"""

from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class LoadSkillInput(BaseModel):
    """Input schema for load_skill tool"""

    skill_name: str = Field(
        description="Name of the skill to load (e.g., 'deep-research', 'git-commit')"
    )


class LoadSkillOutput(BaseModel):
    """Output schema for load_skill tool"""

    skill_name: str
    content: str
    loaded: bool
    error: str | None = None


def load_skill_from_file(skill_name: str, skills_root: str | None = None) -> LoadSkillOutput:
    """Load a skill's SKILL.md content from the filesystem.

    Args:
        skill_name: Name of the skill directory (e.g., 'deep-research')
        skills_root: Root directory containing skill subdirectories.
                    Defaults to /.skills/builtin (container path).

    Returns:
        LoadSkillOutput with skill content or error message
    """
    if skills_root is None:
        skills_root = "/.skills/builtin"

    skill_path = Path(skills_root) / skill_name / "SKILL.md"

    if not skill_path.exists():
        available = []
        root = Path(skills_root)
        if root.exists():
            available = [d.name for d in root.iterdir() if d.is_dir()]
        return LoadSkillOutput(
            skill_name=skill_name,
            content="",
            loaded=False,
            error=f"Skill '{skill_name}' not found. Available skills: {', '.join(available) if available else 'none'}",
        )

    try:
        content = skill_path.read_text(encoding="utf-8")
        return LoadSkillOutput(
            skill_name=skill_name,
            content=content,
            loaded=True,
            error=None,
        )
    except Exception as e:
        return LoadSkillOutput(
            skill_name=skill_name,
            content="",
            loaded=False,
            error=f"Failed to read skill: {str(e)}",
        )


def create_load_skill_tool() -> StructuredTool:
    """Create a StructuredTool for loading skill content.

    Returns:
        StructuredTool instance for loading skills
    """
    return StructuredTool.from_function(
        func=load_skill_from_file,
        name="load_skill",
        description="Load the content of a skill by name. "
        "Returns the full SKILL.md content including instructions. "
        "Use this to quickly access skill workflows and methodologies. "
        "Available skills: deep-research, git-commit, web-artifacts-builder",
        args_schema=LoadSkillInput,
    )
