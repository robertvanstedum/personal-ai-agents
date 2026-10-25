---
name: minimoi-operations
description: Investigate MiniMoi disk pressure, runtime health, build progress and operational questions with current host evidence.
---

# Investigate before advising

Use available native tools. Locate the MiniMoi repository and read its AGENTS.md
and current work record. On Robert's Mac the repository is
`/Users/vanstedum/Projects/personal-ai-agents`; do not assume that path on another host.

## Disk and performance

For every current-space question, run a fresh check in this turn. Start with
`date -u`, `hostname`, `df -h /`, and host memory/swap tools. On macOS use
`diskutil info -plist /` and `diskutil apfs list -plist` to identify the root
volume's APFS container and its CapacityCeiling and CapacityFree.
Container space used is CapacityCeiling minus CapacityFree; do not rely on a
CapacityInUse field (it is absent on this Mac). Lead with that container's available and total space; do not call
the root system volume's Used or capacity percentage the whole Mac's usage.
Do not sum APFS volumes that share a container. If the container check fails,
report available space from df with limited scope, omitting a whole-disk used
figure. Include the tool observation time and host. Inspect
`docker system df` and a bounded `docker ps` summary if Docker is reachable.
Use targeted `du` on known development directories instead of scanning personal
documents. Explain physical disk, Docker virtual-disk reclamation, RAM and swap
separately. A stopped gateway is not a memory leak.

For worktrees use `git worktree list --porcelain`, per-worktree Git status,
branch remote/merge evidence and service mount/process bindings. Worktrees are
checkouts sharing Git history; uncommitted files and unpushed commits can be
unique. Never label one safe to delete from age or branch name alone. Prepare
a cleanup proposal; apply only existing explicit approval, rechecking first.

## Processes and jobs

`python -m core.domain_operations --catalog config/operations/dev.inventory.json status`
is available when that build is present. It is a proposed/incomplete inventory,
not an enabled controller. Also inspect actual Docker state without dumping
environment variables or secrets. Use the existing scheduled-jobs reports and
memory matrix when present; Unknown is not healthy. Do not run generic Compose
up: it can replace unrelated pinned services.

## Build inquiries

Read `_working/memory-build/PROGRESS_FEED_2026-10-04.md` and the latest relevant
handoff for memory work, or the task's own record for other builds. Include the
record timestamp. Native Codex/Claude session catalogs may expose current or
stored work, but a stored session has unknown activity. Use a verified existing
session connection for inquiries. Do not resume/fork it or claim a message was
delivered when there is only a handoff file. Inspect GitHub issues/PRs through
the configured native tools where authorized; preserve private evidence locally.

## Report back

Lead with the answer and cite the tool observation/time or work record. Separate
confirmed causes from hypotheses. State what you did, what changed, verification,
and the next action. If a dependency is offline, report last known evidence and
what must reconnect. Memory search can help later; never require it for a live
disk/service/build investigation.
