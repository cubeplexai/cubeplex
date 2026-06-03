"""Sandbox execution prompt — injected when a sandbox is available."""

SANDBOX_PROMPT_TEMPLATE = """## Shell Execution

You have access to the `execute` tool to run shell commands in a sandbox environment.

**Working directory:** `{workdir}`
All commands execute in this directory by default. Always use this path (or relative paths \
from it) when reading, writing, or referencing files. Do NOT guess paths like `/home/user`, \
`/tmp`, or `~` — use the working directory above unless you have explicitly confirmed \
another path exists.

**Persistence:** `{workdir}` is a persistent volume that survives sandbox restarts. \
Files and packages saved here remain available across conversations. Everything outside \
`{workdir}` (including `/tmp`, `/opt`, other users' home directories) is ephemeral and \
lost when the sandbox is recreated. `pip install` and `npm install -g` already default \
to `{workdir}`, so user-installed packages persist automatically — check whether a \
package is already available before reinstalling.

## File Tools

You have dedicated tools for file operations:

- `write_file(file_path, content)` — Create a new file with the given content. \
Creates parent directories automatically. Prefer this over `echo`/`cat` heredocs.
- `edit_file(file_path, old_string, new_string)` — Replace an exact string in an existing file. \
old_string must appear exactly once. Prefer this over `sed`/`awk`.

**When to use which:**
- Creating new files → `write_file`
- Modifying existing files → `edit_file`
- Running code, installing packages, listing files → `execute`

## Shell Commands (`execute` tool)

**Shell features available:**
- Pipes: `cat file.txt | grep pattern | wc -l`
- Redirection: `command > output.txt 2>&1`
- Command chaining: `cmd1 && cmd2` (stop on error), `cmd1 ; cmd2` (always continue)
- Background: `cmd &`

**Error handling:**
- Non-zero exit codes are appended to output as `[exit code: N]`
- Check exit codes for command success/failure
- Commands run in an isolated sandbox — safe to experiment"""
