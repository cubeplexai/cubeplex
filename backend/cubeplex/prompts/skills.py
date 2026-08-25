"""Available-skills system prompt template."""

# The run manager formats this template with the workspace's discovered skills.
SKILLS_PROMPT_TEMPLATE = """\

# Available skills

{skills_list}

Use `load_skill(name)` to read a skill's instructions. Its result includes a
`path` field — the exact sandbox directory holding that skill's sibling files
(scripts, templates, references). Reference those files using that `path`
verbatim; do not construct the path from the skill name yourself.

If compacted conversation context identifies a "Previously loaded skill" but
its full instructions are no longer visible, call `load_skill(name)` again
before continuing when that skill remains relevant to the current task.

When one skill clearly matches, load only that skill. When descriptions leave
multiple skills plausibly overlapping, load the smallest plausible set before
acting, normally two and no more than three. Compare their full instructions,
choose one primary workflow for the task's dominant requirement, and use other
skills only for distinct, non-conflicting support. Do not load every available
skill defensively or blend conflicting workflows.
"""
