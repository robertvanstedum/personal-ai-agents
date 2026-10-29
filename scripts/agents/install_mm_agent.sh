#!/bin/sh
# Install mm-claude, mm-codex and mm-grok into ~/.local/bin by copy (operations, 28 Oct 2026).
# They start each tool on its plan sign-in with a clean environment: see mm_agent.py.
# Re-run after a change to mm_agent.py; the installed copy does not follow the repo.
set -eu
src="$(cd "$(dirname "$0")" && pwd)/mm_agent.py"
dest="${HOME}/.local/bin"
mkdir -p "$dest"
install -m 0755 "$src" "$dest/mm-agent"
for tool in claude codex grok; do
  ln -sfn mm-agent "$dest/mm-$tool"
done
echo "installed: $dest/mm-agent and mm-claude, mm-codex, mm-grok"
