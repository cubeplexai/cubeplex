"""Skills system prompt template — injected by SkillsMiddleware."""

# This is a template — formatted by SkillsMiddleware with discovered skills
SKILLS_PROMPT_TEMPLATE = """## Available Skills

Skills are pre-defined workflows stored as SKILL.md files. Use them for common tasks.

{skills_list}

To invoke a skill, read its SKILL.md file first, then follow the instructions within it."""
