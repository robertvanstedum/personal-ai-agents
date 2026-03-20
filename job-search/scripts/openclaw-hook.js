/**
 * openclaw-hook.js — Job Search command registry for OpenClaw
 *
 * Registered commands (section 5.1 of JOB_SEARCH_FUNCTIONAL_DESIGN.md):
 *
 *   job-search:daily-run          search + score + digest → Telegram 07:15
 *   job-search:check-portals      poll portal_url for status changes → update applications.json
 *   job-search:score-queue        re-rank opportunities.json (run after new batch)
 *   job-search:draft-outreach     Sonnet drafts message for given OPP-id or APP-id
 *   job-search:add-application    manual entry via OpenClaw prompt
 *   job-search:status-report      regenerate STATUS.md
 *   job-search:high-match-alert   fires immediately when score ≥ 90 (event-driven)
 *
 * Status: REGISTERED — implementation deferred to Claude Code session 1 (v1.0 build scope)
 */

const COMMANDS = {
  'job-search:daily-run':        { script: 'scripts/digest.js',        description: 'Run search + score + digest → Telegram 07:15' },
  'job-search:check-portals':    { script: 'scripts/portal-monitor.js', description: 'Poll portal URLs for status changes' },
  'job-search:score-queue':      { script: 'scripts/score.js',          description: 'Re-rank opportunities.json' },
  'job-search:draft-outreach':   { script: 'lib/outreach.js',           description: 'Draft outreach for OPP-id or APP-id (Sonnet)' },
  'job-search:add-application':  { script: 'scripts/add-application.js',description: 'Log a new application manually' },
  'job-search:status-report':    { script: 'scripts/report.js',         description: 'Regenerate STATUS.md' },
  'job-search:high-match-alert': { script: 'scripts/score.js',          description: 'Immediate Telegram alert when score ≥ 90' },
};

module.exports = { COMMANDS };
