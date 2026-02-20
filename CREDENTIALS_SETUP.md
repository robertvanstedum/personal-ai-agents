# Credential Security Setup

## Overview

Multi-layer approach to protect your credentials:
1. **macOS Keychain** (preferred) - OS-level encryption
2. **.env file** (fallback) - gitignored, local only
3. **Never in code** - always loaded at runtime

## Setup Steps

### Step 1: Install Required Packages

```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
pip install keyring python-dotenv
```

### Step 2: Store Your Geopolitical Futures Credentials

**Option A: Keychain (Recommended)**

```bash
python credential_manager.py store geopolitical_futures
```

It will prompt:
```
Username: [type your username]
Password: [type your password - hidden]
```

Credentials are now encrypted in macOS Keychain.

**Option B: .env File (Fallback)**

1. Copy the template:
```bash
cp .env.example .env
```

2. Edit `.env` and fill in your credentials:
```bash
nano .env
```

Replace:
```
GEOPOLITICAL_FUTURES_USERNAME=your_actual_username
GEOPOLITICAL_FUTURES_PASSWORD=your_actual_password
```

Save and exit (Ctrl+X, Y, Enter)

### Step 3: Test It Works

```bash
python credential_manager.py test geopolitical_futures
```

Should output:
```
✅ Loaded geopolitical_futures credentials from keychain
✅ Found credentials for geopolitical_futures
   Username: your_username
   Password: **************
```

---

## Security Guarantees

### ✅ What's Protected

1. **Keychain credentials:**
   - Encrypted by macOS
   - Requires your user account login
   - Not accessible to other users
   - Protected from malware (user consent required)

2. **.env file:**
   - `.gitignore` prevents git commits
   - File permissions (only your user can read)
   - Not shared publicly

3. **Repository:**
   - `.env.example` has no real credentials
   - `.gitignore` blocks `.env` from commits
   - GitHub never sees your passwords

### ⚠️ What's Not 100% Safe

1. **If someone has physical access to your MacBook:**
   - They can access keychain if they know your Mac password
   - They can read `.env` if they access your files

2. **If malware runs with your user permissions:**
   - Could potentially access keychain (with OS prompts)
   - Could read `.env` file

3. **If you accidentally commit `.env`:**
   - Git history would contain credentials
   - Would need to revoke/change passwords

### 🛡️ Additional Hardening (Optional)

**For paranoid-level security:**

1. **Encrypt .env with GPG:**
```bash
# Encrypt
gpg -c .env  # creates .env.gpg

# Decrypt when needed
gpg -d .env.gpg > .env
```

2. **Use 1Password CLI:**
```bash
# Store in 1Password
op item create --category=login \
  --title="Geopolitical Futures" \
  username=xxx password=yyy

# Retrieve in scripts
op read "op://Private/Geopolitical Futures/username"
```

3. **FileVault full-disk encryption:**
Already enabled on most modern Macs (check System Preferences → Security)

---

## Adding More Credentials

### New Service (e.g., Chicago Tribune)

**Keychain:**
```bash
python credential_manager.py store chicago_tribune
```

**.env:**
Add to `.env`:
```
CHICAGO_TRIBUNE_USERNAME=your_username
CHICAGO_TRIBUNE_PASSWORD=your_password
```

**In your code:**
```python
from credential_manager import get_credentials

creds = get_credentials("chicago_tribune")
```

---

## Credential Rotation

If you need to change a password:

**Keychain:**
```bash
# Delete old
python credential_manager.py delete geopolitical_futures

# Store new
python credential_manager.py store geopolitical_futures
```

**.env:**
Just edit `.env` and update the value.

---

## Emergency: Credentials Leaked

If you accidentally commit `.env` or expose credentials:

1. **Change passwords immediately** on the service
2. **Remove from git history:**
```bash
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch .env' \
  --prune-empty --tag-name-filter cat -- --all
git push origin --force --all
```
3. **Update stored credentials** (keychain or .env)

---

## Security Checklist

Before committing any code:

- [ ] `.env` is in `.gitignore`
- [ ] No credentials in Python files
- [ ] `.env.example` has no real values
- [ ] `git status` doesn't show `.env`
- [ ] Credentials loaded via `credential_manager.get_credentials()`

---

## Questions?

**"Can I use this on a server?"**
Yes, but use `.env` method (keychain requires GUI). See `PRODUCTION_SECURITY.md` for server migration plan.

**"What if I lose my credentials?"**
Keychain is backed up with Time Machine. `.env` is just a file - back it up separately if needed.

**"Can Mini-moi see my passwords?"**
In this session, yes (you're about to give them to me). In code/git, no (never stored). Best practice: use keychain so passwords never appear in chat.

## Server Migration Note

**When moving to Mac Mini / VPS (April 2026):**
- Keychain won't work on headless server
- Switch to encrypted .env or Docker secrets
- See `PRODUCTION_SECURITY.md` for full migration guide
- `credential_manager.py` already supports fallback (no code changes)

---

_Last updated: 2026-02-11_
