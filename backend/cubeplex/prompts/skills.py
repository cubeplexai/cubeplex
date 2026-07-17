"""Skills system prompt template — injected by SkillsMiddleware."""

# This is a template — formatted by SkillsMiddleware with discovered skills
SKILLS_PROMPT_TEMPLATE = """\

# Available skills

{skills_list}

Use `load_skill(name)` to read a skill's instructions. Its result includes a
`path` field — the exact sandbox directory holding that skill's sibling files
(scripts, templates, references). Reference those files using that `path`
verbatim; do not construct the path from the skill name yourself.
"""
