# Spec 1 — Auth + Multi-User Account System
*Created: 2026-06-24 — Claude.ai*
*Status: spec_ready*
*Part of: Portuguese Domain + Multi-User Platform*
*Gates: all other Portuguese domain specs*

---

## Goal

Replace the current single-user Google OAuth setup with a proper
multi-user account system. Request Access → Robert approves →
user creates account with email + password → standard forgot/reset
flow. Robert controls who gets in.

---

## Current state

One user (Robert) authenticated via Google OAuth. No account
creation, no password management, no user roles. Works for a
single operator, not for multiple users.

---

## New auth model

### Roles

| Role | Who | Can do |
|------|-----|--------|
| admin | Robert (+ wife if added) | All domains, all user management, feedback review, persona moderation |
| user | Daughters (approved) | Assigned domains only, own session history, create personas within limits |

### Flow

```
1. User lands on preview page
2. Clicks "Request Access" for Portuguese domain
3. Fills simple form: name, email, why they want access
4. Robert gets Telegram notification via system_bot:
   "New access request: [name] ([email]) — Portuguese domain"
   With inline /approve_[id] and /reject_[id] commands
5. Robert approves
6. User gets email with account creation link (expires 48h)
7. User sets password (min 8 chars, standard strength rules)
8. User logs in with email + password
9. Access to approved domains only
```

### Forgot password flow

```
Login page → "Forgot password?" link
    ↓
Enter email address
    ↓
If account exists: reset link sent (expires 1h)
If no account: same response (don't reveal account existence)
    ↓
User clicks link → sets new password
    ↓
Redirected to login
```

---

## Database schema

New tables in `auth.*` schema:

```sql
CREATE SCHEMA auth;

CREATE TABLE auth.users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(50) DEFAULT 'user',
    created_at      TIMESTAMP DEFAULT NOW(),
    last_login      TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE
);

CREATE TABLE auth.domain_access (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    domain      VARCHAR(50) NOT NULL,  -- 'german', 'portuguese', 'curator'
    granted_at  TIMESTAMP DEFAULT NOW(),
    granted_by  INTEGER REFERENCES auth.users(id),
    UNIQUE(user_id, domain)
);

CREATE TABLE auth.access_requests (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    domain          VARCHAR(50) NOT NULL,
    reason          TEXT,
    requested_at    TIMESTAMP DEFAULT NOW(),
    status          VARCHAR(50) DEFAULT 'pending',
    actioned_at     TIMESTAMP,
    actioned_by     INTEGER REFERENCES auth.users(id)
);

CREATE TABLE auth.password_reset_tokens (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    token       VARCHAR(255) UNIQUE NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    expires_at  TIMESTAMP NOT NULL,
    used        BOOLEAN DEFAULT FALSE
);

CREATE TABLE auth.account_creation_tokens (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL,
    domain      VARCHAR(50) NOT NULL,
    token       VARCHAR(255) UNIQUE NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    expires_at  TIMESTAMP NOT NULL,
    used        BOOLEAN DEFAULT FALSE
);
```

---

## Password handling

Use `bcrypt` for password hashing. Never store plaintext.

```python
from flask_bcrypt import Bcrypt
bcrypt = Bcrypt(app)

# Hash on creation
password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

# Verify on login
bcrypt.check_password_hash(user.password_hash, password)
```

Add `flask-bcrypt` to portal requirements.txt.

---

## Session management

Use Flask-Login. Session stored server-side (existing session
config). Add `login_required` decorator to all protected routes.

```python
from flask_login import LoginManager, login_required, current_user

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return auth.users.get(user_id)
```

Domain access check decorator:

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

# Usage
@app.route('/portuguese/leitura')
@requires_domain('portuguese')
def leitura():
    ...
```

---

## Email delivery

Use AWS SES (consistent with existing AWS infrastructure).

```python
import boto3

ses = boto3.client('ses', region_name='us-east-1')

def send_account_creation_email(email, name, token):
    link = f"https://minimoi.ai/create-account/{token}"
    ses.send_email(
        Source='noreply@minimoi.ai',
        Destination={'ToAddresses': [email]},
        Message={
            'Subject': {'Data': 'Your mini-moi access is ready'},
            'Body': {
                'Text': {'Data': f"Hi {name},\n\nYour access has been approved.\n\nCreate your account here (link expires in 48 hours):\n{link}\n\nmini-moi"}
            }
        }
    )
```

Add SES sending permission to EC2 IAM role.
Verify `noreply@minimoi.ai` in SES (or use Robert's email as sender).

---

## Telegram approval flow

When a request comes in, system_bot sends:

```
🔔 Access Request
Name: Sofia van Stedum
Email: sofia@example.com
Domain: Portuguese
Reason: My dad said I could try it

/approve_42  /reject_42
```

system_bot handles `/approve_[id]` and `/reject_[id]` commands:
- approve → creates account_creation_token → sends email
- reject → updates status → optionally sends rejection email

---

## Portal routes (new)

```
GET  /login                    Login page
POST /login                    Authenticate
GET  /logout                   Clear session
GET  /request-access           Access request form
POST /request-access           Submit request
GET  /create-account/<token>   Account creation page
POST /create-account/<token>   Set password + create account
GET  /forgot-password          Forgot password form
POST /forgot-password          Send reset email
GET  /reset-password/<token>   Reset password page
POST /reset-password/<token>   Set new password
```

---

## Robert admin routes (Guild UI)

```
GET  /guild/users              User list with domain access
GET  /guild/access-requests    Pending requests
POST /guild/approve/<id>       Approve request (also available via Telegram)
POST /guild/reject/<id>        Reject request
```

---

## Migration — existing Robert account

Robert currently uses Google OAuth. After this build:
- Robert gets an admin account created directly (no request flow)
- Google OAuth stays active for Robert only (don't break existing login)
- New users use email/password only
- Long-term: Robert can switch to email/password too

---

## Definition of Done

- [ ] auth.* schema created and migrated
- [ ] Login page (email + password) working
- [ ] Robert's existing Google OAuth login still works
- [ ] Request Access form on preview page (per domain)
- [ ] Robert gets Telegram notification on new request
- [ ] /approve and /reject commands work in system_bot
- [ ] Account creation email sent on approval (AWS SES)
- [ ] Account creation flow working (token link → set password)
- [ ] Forgot password flow working (email → reset link → new password)
- [ ] Flask-Login session management working
- [ ] requires_domain() decorator protecting domain routes
- [ ] Admin user list in Guild UI showing all users + access
- [ ] Pending access requests visible in Guild UI
- [ ] Robert admin account created directly (no request flow)
- [ ] Test: daughter creates account, logs in, sees Portuguese only
- [ ] Test: daughter cannot access German domain
- [ ] Test: Robert can see daughter's access in Guild UI

## Commit message

`Auth: multi-user account system — request/approve flow, email/password
login, forgot/reset, domain access control, AWS SES email delivery`

---

*Spec 1 · Portuguese Domain series · 2026-06-24 · Claude.ai*
*Next: Spec 2 — Portuguese domain shell*
