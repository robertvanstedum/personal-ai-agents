# Spec 1 — Auth + Multi-User Account System
*Created: 2026-06-24 — Claude.ai*
*Updated: 2026-06-24 — simplified per Grok review*
*Status: spec_ready*
*Gates on: Block E complete (CI/CD + regression tests green)*

---

## Goal

Multi-user accounts for Portuguese domain launch. Three daughters
get access. Robert approves via the existing CoS/Telegram flow.
Simple, working, not over-engineered for three users.

---

## What already exists (don't rebuild)

`guild.guest_requests` table and the CoS approval flow are already
live. Robert gets a Telegram ping when someone requests access,
can approve or reject inline. Spec 1 extends this — it does not
replace it.

Changes to existing flow:
- Add `domain` column to `guild.guest_requests`
- Wire approval action to account creation instead of just logging

---

## Simplified auth flow

```
User lands on preview → clicks "Request Access" (Portuguese domain)
    ↓
Form: name, email, reason, domain (hidden field)
    ↓
Logged to guild.guest_requests (existing table + domain column)
    ↓
Robert gets Telegram notification via system_bot (existing flow):
  "🔔 Access Request
   Name: Ana Silva
   Email: ana@example.com
   Domain: Portuguese
   /approve_42  /reject_42"
    ↓
Robert clicks /approve_42
    ↓
System creates account + emails one-time login link
    ↓
User clicks link → logged in → lands on Portuguese domain
    ↓
User sets password from profile page (one step, not a separate flow)
```

No magic link token management complexity. No separate account
creation flow. Approve → account exists → email sent → done.

---

## Database schema

**Modify existing table (migration):**
```sql
ALTER TABLE guild.guest_requests
ADD COLUMN domain VARCHAR(50) DEFAULT 'portuguese',
ADD CONSTRAINT guest_requests_status_check
  CHECK (status IN (
    'pending', 'granted', 'rejected', 'approved_pending_email'
  ));
```

**New tables (auth only):**
```sql
CREATE SCHEMA auth;

CREATE TABLE auth.users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),          -- nullable until user sets it
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(50) DEFAULT 'user',  -- 'user' or 'admin'
    created_at      TIMESTAMP DEFAULT NOW(),
    last_login      TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE
);

CREATE TABLE auth.domain_access (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    domain      VARCHAR(50) NOT NULL,
    granted_at  TIMESTAMP DEFAULT NOW(),
    granted_by  INTEGER REFERENCES auth.users(id),
    UNIQUE(user_id, domain)
);

CREATE TABLE auth.login_tokens (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    token       VARCHAR(255) UNIQUE NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    expires_at  TIMESTAMP NOT NULL,        -- 48h
    used        BOOLEAN DEFAULT FALSE
);
```

Three tables. No audit log table, no password reset tokens, no
account creation tokens — these can be added when real usage
reveals they're needed.

---

## Password handling

```python
from flask_bcrypt import Bcrypt
bcrypt = Bcrypt(app)

# Set on first login via token link
password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
bcrypt.check_password_hash(user.password_hash, password)
```

User sets their own password after first login. No temporary
passwords sent via email (security antipattern).

---

## Session management

Flask-Login. Standard session cookie: HttpOnly, Secure, SameSite=Lax.

```python
from flask_login import LoginManager, login_required, current_user
login_manager = LoginManager(app)
login_manager.login_view = 'login'
```

Domain access decorator:
```python
def requires_domain(domain):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if not has_domain_access(current_user.id, domain):
                return render_template('access_denied.html'), 403
            return f(*args, **kwargs)
        return decorated
    return decorator
```

---

## Email delivery (AWS SES)

One email type: one-time login link on approval.

```python
import boto3
ses = boto3.client('ses', region_name='us-east-1')

def send_login_link(email, name, token):
    link = f"https://minimoi.ai/login/{token}"
    ses.send_email(
        Source='noreply@minimoi.ai',
        Destination={'ToAddresses': [email]},
        Message={
            'Subject': {'Data': 'Your mini-moi access is ready'},
            'Body': {
                'Text': {'Data': (
                    f"Hi {name},\n\n"
                    f"Your access has been approved.\n\n"
                    f"Click here to log in (link expires in 48 hours):\n"
                    f"{link}\n\n"
                    f"You'll be able to set your password after logging in.\n\n"
                    f"mini-moi"
                )}
            }
        }
    )
```

**Robert action:** verify minimoi.ai domain in AWS SES before
Spec 1 deploys.

---

## Telegram approval (extend existing system_bot)

Extend existing `/approve_[id]` handler in system_bot to:
1. Create auth.users record
2. Grant domain access in auth.domain_access
3. Generate login token (48h expiry, `secrets.token_urlsafe(32)`)
4. Send login link email via SES
5. Update guild.guest_requests status to 'granted'
6. On SES failure: set status to 'approved_pending_email', alert Robert via Telegram

This is an extension of the existing handler, not a new one.

---

## Routes (new)

```
GET/POST  /request-access          Request form (domain as hidden field)
GET       /login/<token>           One-time login link → sets session
GET/POST  /login                   Email + password login (returning users)
GET       /logout                  Clear session
GET/POST  /profile/password        Set/change password (after first login)
GET       /guild/users             Admin: user list with domain access
GET       /guild/access-requests   Admin: pending requests (existing page, add domain column)
```

---

## Migration — Robert's existing account

Robert keeps Google OAuth. New users use email + token login
initially, then email + password for return visits.

Robert admin account: role='admin' set directly in database.
Wife admin account: same, if Robert decides yes.

---

## Security considerations

- **Token entropy:** All tokens generated with `secrets.token_urlsafe(32)` — cryptographically secure, not predictable
- **Token expiry:** Login tokens expire in 48 hours, single-use only
- **Session cookies:** HttpOnly, Secure, SameSite=Lax — set on all sessions
- **PII scope:** Limited to name + email for initial users — no additional personal data collected
- **Rate limiting:** Deferred for three-user initial rollout. Add before any public-facing launch. Endpoints to rate-limit: `/login`, `/request-access`, `/login/<token>`
- **Email delivery failure:** If SES send fails after approval, log the error, leave the request in `approved_pending_email` status, alert Robert via Telegram. Token remains valid — Robert can retry the email send from Guild UI.
- **requires_domain() coverage:** Decorator must be applied to every route under the Portuguese domain without exception. Claude Code to audit all routes on completion.

---

## What's deferred (add when real usage shows it's needed)

- Full password reset flow (forgot password email)
- Rate limiting on auth endpoints
- Audit log table
- Comprehensive token management
- Email templates with HTML formatting

For three daughters, none of these are needed on day one.

---

## Test coverage

**Auth flow tests (Tier 2 — added in this commit):**
- Unauthenticated request to protected route → redirect to login
- One-time login token: valid → creates session, marks token used
- One-time login token: expired → rejected
- One-time login token: already used → rejected
- User with Portuguese access → 200 on /portuguese/
- User without Portuguese access → 403 on /portuguese/
- User without Portuguese access → cannot reach /german/ either
- Admin user → can reach /guild/users
- Non-admin user → cannot reach /guild/users
- Same login token used from two different browsers → second use rejected
- All Portuguese domain routes protected by requires_domain() → spot-check minimum 3 routes

---

## Definition of Done

- [ ] `domain` column added to guild.guest_requests
- [ ] auth.* schema (3 tables) created and migrated
- [ ] Request Access form on preview page (domain as hidden field)
- [ ] Existing Telegram approval flow extended to create account + send email
- [ ] One-time login link flow working end to end
- [ ] Email sends via AWS SES (minimoi.ai domain verified)
- [ ] User can set password after first login (/profile/password)
- [ ] Email + password login works for returning users
- [ ] Robert keeps Google OAuth login
- [ ] requires_domain() protecting all domain routes
- [ ] Guild UI: user list shows users + domain access
- [ ] Guild UI: access requests show domain column
- [ ] Robert admin account set directly
- [ ] All Tier 2 auth tests passing in CI

## Commit message

`Auth: multi-user accounts — extend guest_requests with domain,
one-time login link on approval, Flask-Login, bcrypt, domain
access control, AWS SES email`

---

*Spec 1 · Portuguese Domain series · 2026-06-24 · Claude.ai*
*Simplified from original — leverages existing guest_requests flow*
*Gates on: Block E Definition of Done complete*
*Next: Spec 2 — Portuguese domain shell*
