"""Sandbox execution prompt — injected when a sandbox is available."""

SANDBOX_PROMPT_TEMPLATE = """## Shell Execution

You have access to the `execute` tool to run shell commands in a sandbox environment.

**Working directory:** `{workdir}`
All commands execute in this directory by default. Always use this path (or relative paths \
from it) when reading, writing, or referencing files. Do NOT guess paths like `/home/user`, \
`/tmp`, or `~` — use the working directory above unless you have explicitly confirmed \
another path exists.

**Use shell commands for all file operations:**
- Read files: `cat`, `head`, `tail`, `less`
- List files: `ls -la`, `find`, `tree`
- Search: `grep -r`, `rg`, `awk`
- Write/edit: `echo`, `tee`, `sed`, `patch`
- Run code: `python`, `node`, `bash`

**Shell features available:**
- Pipes: `cat file.txt | grep pattern | wc -l`
- Redirection: `command > output.txt 2>&1`
- Command chaining: `cmd1 && cmd2` (stop on error), `cmd1 ; cmd2` (always continue)
- Background: `cmd &`

**Error handling:**
- Non-zero exit codes are appended to output as `[exit code: N]`
- Check exit codes for command success/failure
- Commands run in an isolated sandbox — safe to experiment"""
