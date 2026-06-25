# Spec 1 — Auth + Multi-User Account System
*Created: 2026-06-24 — Claude.ai*
*Finalized: 2026-06-25 — all decisions locked*
*Status: spec_ready — build complete, this is the canonical reference*
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
   Name: [user name]
   Email: user@example.com
   Domain: Portuguese
   /approve_42  /reject_42"
    ↓
Robert clicks /approve_42
    ↓
System creates account + emails one-time login link (48h expiry)
    ↓
User clicks link → logged in → lands on Portuguese domain
    ↓
User sets password from profile page (one step, not a separate flow)
```

---

## Three-tier role system (locked)

| Role | Who | Can do |
|------|-----|--------|
| owner | Robert | Everything. Set directly in DB — never grantable via normal flow. |
| admin | Wife (or future trusted person) | Admin tab, feedback review, user management. Cannot grant owner. |
| user | Daughters | Their assigned domains, own sessions, own personas. |

```python
def is_owner(user=None):
    u = user or current_user
    return u.is_authenticated and u.role == 'owner'

def is_admin(user=None):
    u = user or current_user
    return u.is_authenticated and u.role in ('owner', 'admin')

def is_user(user=None):
    u = user or current_user
    return u.is_authenticated
```

---

## Database schema

**Modify existing table:**
```sql
ALTER TABLE guild.guest_requests
ADD COLUMN domain VARCHAR(50) DEFAULT 'portuguese';

ALTER TABLE guild.guest_requests
ADD CONSTRAINT guest_requests_status_check
  CHECK (status IN (
    'pending', 'granted', 'rejected', 'approved_pending_email'
  ));
```

**New tables:**
```sql
CREATE SCHEMA auth;

CREATE TABLE auth.users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(50) DEFAULT 'user',
    created_at      TIMESTAMP DEFAULT NOW(),
    last_login      TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE,
    CONSTRAINT users_role_check
      CHECK (role IN ('owner', 'admin', 'user'))
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

---

## Password handling

```python
from flask_bcrypt import Bcrypt
bcrypt = Bcrypt(app)

password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
bcrypt.check_password_hash(user.password_hash, password)
```

User sets their own password after first login. No temporary
passwords sent via email.

---

## Session management

Flask-Login. Session cookie: HttpOnly, Secure, SameSite=Lax.

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
def send_login_link(email, name, token):
    link = f"https://minimoi.ai/login/{token}"
    # Dev escape hatch — uses role system, never prints in production
    from utils.role import is_production
    if not is_production():
        print(f"🔗 DEV login link: {link}")
    ses.send_email(
        Source='noreply@minimoi.ai',
        Destination={'ToAddresses': [email]},
        Message={
            'Subject': {'Data': 'Your mini-moi access is ready'},
            'Body': {
                'Text': {'Data': (
                    f"Hi {name},\n\n"
                    f"Your access has been approved.\n\n"
                    f"Click here to log in "
                    f"(link expires in 48 hours):\n"
                    f"{link}\n\n"
                    f"You'll be able to set your password "
                    f"after logging in.\n\n"
                    f"mini-moi"
                )}
            }
        }
    )
```

**Email failure handling:** If SES fails, log the error, set status
to `approved_pending_email`, alert Robert via Telegram. Token remains
valid — Robert can retry from Guild UI.

**Robert action:** verify minimoi.ai domain in AWS SES before
Spec 1 deploys to production.

---

## Telegram approval (extend existing system_bot)

Extend existing `/approve_[id]` handler to:
1. Create auth.users record (`role='user'`)
2. Grant domain access in auth.domain_access
3. Generate login token (48h expiry, `secrets.token_urlsafe(32)`)
4. Send login link email via SES
5. Update guild.guest_requests status to 'granted'

This is an extension of the existing handler, not a new one.

---

## Routes (all in minimoi_portal/app.py)

```
GET/POST  /request-access          Request form (domain as hidden field)
GET       /login/<token>           One-time login link → sets session
GET/POST  /login                   Email + password login (returning users)
GET       /logout                  Clear session
GET/POST  /profile/password        Set/change password (after first login)
GET       /guild/users             Admin: user list with domain access
GET       /guild/access-requests   Admin: pending requests (+ domain column)
GET       /guild/audit-log         Admin: action log (future — deferred)
```

---

## Migration — existing accounts

Robert: `role='owner'` set directly in database.
Wife (if admin): `role='admin'` set directly in database.
Both bypass the request flow — accounts created directly.

Robert keeps Google OAuth for his own login throughout.
New users (daughters) use email + token login initially,
then email + password for return visits.

---

## Security considerations

- **Token entropy:** `secrets.token_urlsafe(32)` — cryptographically
  secure, not predictable
- **Token expiry:** 48 hours, single-use only
- **Token reuse:** `FOR UPDATE` lock on token consumption prevents
  race conditions on concurrent redemption attempts
- **Session cookies:** HttpOnly, Secure, SameSite=Lax
- **PII scope:** name + email only for initial users
- **Dev escape hatch:** login link prints to logs only when
  `MINIMOI_ROLE != production` — never exposed in production
- **Rate limiting:** Deferred for three-user rollout. Add before
  any public-facing launch. Endpoints: /login, /request-access,
  /login/<token>
- **Email failure:** `approved_pending_email` status, Telegram alert,
  token stays valid for retry
- **requires_domain() coverage:** Applied to every Portuguese route
  without exception. Claude Code audits all routes on completion.

---

## What's deferred

- Full password reset flow (forgot password email)
- Rate limiting on auth endpoints
- Audit log table
- HTML email templates

---

## Test coverage (Tier 2 — added in this commit)

- Unauthenticated request to protected route → redirect to login
- Valid login token → creates session, marks token used
- Expired login token → rejected (400)
- Already-used login token → rejected (400)
- Same token used from two different browsers → second use rejected
- User with Portuguese access → 200 on /portuguese/
- User without Portuguese access → 403 on /portuguese/
- User without Portuguese access → 403 on /german/ too
- Admin user (`role='admin'`) → can reach /guild/users
- Owner user (`role='owner'`) → can reach /guild/users
- Regular user (`role='user'`) → cannot reach /guild/users (403)
- `is_owner()` → True for owner only
- `is_admin()` → True for owner and admin, False for user
- All Portuguese domain routes: spot-check minimum 3 for
  requires_domain() coverage

---

## Definition of Done

- [ ] `domain` column added to guild.guest_requests
- [ ] Status CHECK constraint updated (includes approved_pending_email)
- [ ] auth.* schema (3 tables) with role CHECK constraint
- [ ] `is_owner()`, `is_admin()`, `is_user()` helpers in domain_auth.py
- [ ] Request Access form on preview page
- [ ] Telegram approval flow extended — creates account + sends email
- [ ] 48h one-time login link flow working end to end
- [ ] Email sends via AWS SES (minimoi.ai domain verified)
- [ ] Dev escape hatch: login link in logs when not production
- [ ] User can set password after first login (/profile/password)
- [ ] Email + password login works for returning users
- [ ] Robert keeps Google OAuth login
- [ ] Robert account: role='owner' set directly
- [ ] requires_domain() protecting all domain routes
- [ ] Guild UI: user list shows users + domain access
- [ ] Guild UI: access requests show domain column
- [ ] All Tier 2 auth tests passing in CI

## Commit message

`Auth: multi-user accounts — extend guest_requests with domain,
three-tier roles (owner/admin/user), one-time login link on
approval, Flask-Login, bcrypt, domain access control, AWS SES`

---

*Spec 1 · Portuguese Domain series*
*Canonical version: docs/specs/spec_1_auth_final_2026-06-25.md*
*Supersedes: spec_1_auth_multiuser_2026-06-24.md*
