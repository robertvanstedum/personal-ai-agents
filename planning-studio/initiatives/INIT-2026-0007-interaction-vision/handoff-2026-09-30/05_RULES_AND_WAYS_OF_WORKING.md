# 05: Rules and ways of working

## Hard security rules (still in force)
- **Never sign in to production or dev.minimoi.ai** as an agent, and never use Robert's owner login or the `minimoi-tour-capture` Keychain item.
- **Credentials: Claude names the command and checks, read-only, that the item exists. Robert always runs the command and types the value himself.**
  - Never read, print or log a secret value.
  - Keychain item *names* are fine to reference.
- **Never read `private/career/` or `_working/RVS-Career-Aug26/`.**
- **The repo is public.** No public vulnerability disclosures:
  - track them privately in `_working/security/` (git-ignored);
  - fix PRs get neutral titles and descriptions;
  - review files keep "tracked privately" wording.
- **Never delete `main`.** Preserve before removing anything.
- **Don't launch paid model sessions for testing without asking.** The current authorisation is $10 total for MC and CoS test turns, of which about $0.05 is used.
- **Payment details** are never stored or echoed. Tests mask card-like numbers.
- **The personal OpenClaw** stays separate and is excluded from MiniMoi memory loading.
- **Production changes and protected-document edits** need Robert's OK on a reviewed diff.
- **Stop conditions:** a changed production queue checksum, a failed deploy, an unhealthy service, or a STOP file in `planning-studio/…/reviews/`.

## Roles (how the Sep 29 session ran)
- **This session** handles coordination, integration, staging rollout, reporting to Robert and Telegram status. It is the **only repo writer**: it drives one builder agent at a time, with one writer per branch.
- **The builder agent** (Opus): one PR per concern, off `origin/main` unless explicitly stacked. Commits only, no force-push. It runs the full suite plus the relevant Playwright browser checks, states the release class, and reports the head SHA.
- **The reviewer agents** (Opus, independent, read-only): every PR gets a review file in `reviews/`, then a re-check after the fixes.
  - Security-sensitive PRs go to a security-minded reviewer.
  - Private security reviews go in `_working/security/`.
- **Codex** reviews the actual diffs and designs; its comments are not merge approval.
- **Robert** approves every merge, push to main, deletion and production change.

## Review cadence (Robert, Sep 27)
- **Specs:** two agent reviews.
- **Builds:** no blocking per chunk; proceed when no reviewer is available.
- **End-to-end testing** is done by another agent when the build is done.
- **Minor changes:** one agent's review is enough. Major changes never go without review.

## Release classifier (`scripts/ci/classify_release.py`)
- A push to main redeploys only the classified services.
- Changes to `.github/workflows/deploy.yml` count as a **full** release.
- Paths marked release-only redeploy nothing: `scripts/workshop/`, `scripts/tools/tour_capture/` (#282), and docs.

## Talking to Robert
- **He reads on his phone.** Use plain language, lead with the outcome, and ask decisions briefly with a recommendation.
- **Telegram:** send status at milestones and blockers, and periodically when he's away. See 04 for the helper.
- **When a session gets long,** suggest a clean break.
- **Shell commands for him** go in their own fenced `bash` block.
