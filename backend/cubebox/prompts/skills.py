"""Skills system prompt template — injected by SkillsMiddleware."""

# This is a template — formatted by SkillsMiddleware with discovered skills
SKILLS_PROMPT_TEMPLATE = """\

# Available skills

{skills_list}

Use `load_skill(name)` to read a skill's instructions. Skills' sibling files
(scripts, templates) are available at `/.skills/<name>/<version>/` inside the
sandbox when you actually use them.
"""
