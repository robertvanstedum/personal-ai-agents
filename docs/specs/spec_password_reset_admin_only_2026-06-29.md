# Spec: Password Reset (Admin-Only)
*Created: 2026-06-29 · Claude.ai (design) → Claude Code (build)*

---

## Issue type
Bug fix / essential ops. Not a feature. Track as GitHub issue, fix immediately
after Build 4 (Manual word entry).

---

## Problem

There is currently no way to reset a user password without direct database
access. Robert needs a simple admin-only mechanism. No self-service "forgot
password" flow is needed at this stage — the user base is Robert + Vera +
eventually daughters, all of whom can ask Robert directly.

---

## Scope (what this is)

- Admin can set a new password for any user from the Admin tab
- Simple: type the new password, confirm it, submit
- No email flow, no token, no "forgot password" link for end users
- No complexity requirements beyond minimum length (8 chars)
- Works for any user account (Robert can reset Vera's, his own, etc.)

---

## Scope (what this is NOT)

- No self-service forgot-password flow (deferred — gates on AWS SES domain
  verification for minimoi.ai, which is not yet done)
- No email delivery of reset links
- No audit log of resets (deferred)
- No rate limiting on the reset form (deferred — admin-only, low risk)
- No password complexity rules beyond minimum length

---

## Implementation

### Route
`POST /app/admin/reset-password`
Protected by `@requires_role('owner')` — Robert only, not even admin role.

### Form (in Admin tab, existing admin page)
Simple card under a "User Management" section:

```
User: [dropdown — all users by email]
New password: [password input]
Confirm password: [password input]
[Reset Password button]
```

On submit:
- Validate passwords match
- Validate length ≥ 8 chars
- Hash with bcrypt (same as registration)
- UPDATE auth.users SET password_hash = ... WHERE id = ...
- Flash success message: "Password updated for [email]"
- Flash error if mismatch or too short

### No email, no token, no session invalidation required at this stage.
If Robert resets Vera's password, he tells her the new one directly. Simple.

---

## Definition of Done

- [ ] Admin tab has a visible "User Management" section with the reset form
- [ ] Dropdown populates from auth.users (email + id)
- [ ] Passwords match validation works client-side and server-side
- [ ] Minimum length enforced (8 chars)
- [ ] bcrypt hash used (same function as registration, not a new one)
- [ ] Success and error flash messages display correctly
- [ ] Route protected: `@requires_role('owner')` — 403 for any other role
- [ ] Tested: reset Vera's password, log in as Vera with new password
- [ ] Tested: confirm 403 if accessed as non-owner

---

## Commit

```
feat: admin password reset (owner-only, no email flow)

- New "User Management" section in Admin tab
- Owner can set new password for any user via form
- bcrypt hash, match + length validation, flash feedback
- Route protected by requires_role('owner')
- No self-service flow, no email, no audit log (all deferred)

Closes #[ISSUE_NUMBER]
```

---

## Deferred (when AWS SES is verified)

Self-service forgot-password flow:
- "Forgot password?" link on login page
- Generates time-limited token (24h, secrets.token_urlsafe(32))
- Sends reset link via SES to user's email
- Token redeemed → user sets new password
- Token invalidated after use

Gate: AWS SES domain verification for minimoi.ai. Do not build until that
is complete — the form exists but email delivery will silently fail without it.

---

*Spec · 2026-06-29 · Claude.ai → Claude Code · admin-only, simple, no email*
