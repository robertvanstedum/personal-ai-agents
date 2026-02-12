# Production Security - Server Migration Plan

## Current Setup (MacBook - Development)

**Credential storage:** macOS Keychain  
**Works because:** GUI-based, user-level encryption  
**Good for:** Local development, personal use

---

## Server Migration Requirements (Mac Mini / VPS)

### Problem: Keychain Won't Work on Headless Server

**Why:**
- Keychain requires GUI/user session
- Server runs as daemon/service (no interactive login)
- Different security model needed

### Solution: Tiered Production Security

---

## Phase 5 Production Security Strategy

### Option A: Environment Variables (Server Config)

**How it works:**
```bash
# In server startup script or systemd service
export GEOPOLITICAL_FUTURES_USERNAME="xxx"
export GEOPOLITICAL_FUTURES_PASSWORD="yyy"
```

**Pros:**
- Standard for production apps
- Works on any server
- Not in code or git

**Cons:**
- Visible in process list (`ps aux`)
- Root user can see them

**When to use:** Simple deployments, trusted server

---

### Option B: Docker Secrets (Recommended for Docker)

**How it works:**
```yaml
# docker-compose.yml
services:
  ai-agent:
    secrets:
      - geopolitical_futures_username
      - geopolitical_futures_password

secrets:
  geopolitical_futures_username:
    file: ./secrets/gf_username.txt
  geopolitical_futures_password:
    file: ./secrets/gf_password.txt
```

**Pros:**
- Encrypted at rest
- Mounted as files in container (not env vars)
- Not visible in docker inspect

**Cons:**
- Requires Docker Swarm mode (or external secret manager)
- More complex setup

**When to use:** Docker-based production deployment

---

### Option C: HashiCorp Vault (Enterprise-grade)

**How it works:**
- Central secret storage
- Dynamically generated credentials
- Automatic rotation
- Audit logging

**Pros:**
- Industry standard for production secrets
- Best security
- Supports rotation & revocation

**Cons:**
- Complex to set up
- Overkill for single-user personal agent

**When to use:** If scaling to multiple users or public service

---

### Option D: Cloud Provider Secrets (AWS/GCP/Azure)

**Examples:**
- AWS Secrets Manager
- Google Cloud Secret Manager
- Azure Key Vault

**Pros:**
- Managed service (encrypted, backed up)
- IAM integration
- Automatic rotation support

**Cons:**
- Requires cloud account
- Monthly cost (~$0.40/secret/month)
- Vendor lock-in

**When to use:** If deploying to cloud VPS

---

## Recommended Path for RVS Associates LLC

### Phase 1 (Now): MacBook Development
**Method:** macOS Keychain  
**Security level:** Good for personal use  
**Status:** ✅ Implemented

### Phase 2 (Mac Mini Local Server)
**Method:** Environment variables + encrypted .env  
**Security level:** Good for home/office server  
**Implementation:**
1. Encrypted .env file (GPG)
2. Systemd service loads on startup
3. File permissions (700, owner-only)
4. Not in git, backed up separately

**Setup:**
```bash
# Encrypt .env
gpg --symmetric --cipher-algo AES256 .env  # creates .env.gpg

# In systemd service
ExecStartPre=/usr/bin/gpg --decrypt .env.gpg > /tmp/.env
EnvironmentFile=/tmp/.env
ExecStartPost=/bin/rm /tmp/.env
```

### Phase 3 (Cloud VPS - Public Deployment)
**Method:** Docker Secrets + Cloud Secret Manager  
**Security level:** Production-grade  
**Implementation:**
1. AWS/GCP Secret Manager for sensitive creds
2. Docker secrets for container runtime
3. TLS/HTTPS for all connections
4. Firewall rules (only necessary ports)
5. Automated credential rotation

### Phase 4 (Multi-User / Commercial)
**Method:** HashiCorp Vault  
**Security level:** Enterprise-grade  
**Implementation:**
- Central secret management
- Role-based access control
- Audit logging
- Automatic rotation
- Compliance-ready

---

## Migration Checklist (Mac Mini)

**Before deploying to server:**

- [ ] Audit all credential usage (what needs access?)
- [ ] Choose appropriate secret storage (env vars or Docker secrets)
- [ ] Encrypt production .env with GPG
- [ ] Set file permissions (700 on secrets directory)
- [ ] Document secret rotation procedure
- [ ] Set up backup strategy for secrets
- [ ] Test credential loading in server environment
- [ ] Remove any hardcoded credentials
- [ ] Enable full-disk encryption on server
- [ ] Configure firewall rules
- [ ] Set up monitoring/alerting for security events

---

## Current Code Compatibility

**Good news:** `credential_manager.py` already supports fallback!

```python
# Tries keychain first (dev)
# Falls back to .env (server)
# Fails gracefully with helpful error

creds = get_credentials("geopolitical_futures")
```

**No code changes needed** for server migration - just change how credentials are stored (keychain → .env on server).

---

## Security Hardening Roadmap

### Week of Feb 17-23 (Phase 2 prep)
- [ ] Test .env fallback mode
- [ ] Document Mac Mini deployment steps
- [ ] Create systemd service template
- [ ] Write backup/restore procedure

### March (Phase 4)
- [ ] Set up Mac Mini test environment
- [ ] Test encrypted .env loading
- [ ] Validate all credential flows
- [ ] Document production runbook

### April (Phase 5 - Production)
- [ ] Deploy with production secrets
- [ ] Enable monitoring for failed auth attempts
- [ ] Set up credential rotation schedule
- [ ] Penetration testing (optional)

---

## Threat Model (Production)

**Protect against:**
1. ✅ Code/git exposure (never committed)
2. ✅ Unauthorized server access (encrypted secrets)
3. ✅ Process inspection (not in env vars or process list)
4. ✅ Disk theft (full-disk encryption)
5. ⏳ Credential compromise (rotation procedure needed)

**Accept risk:**
- Root access to server = full access (inevitable)
- Physical access to unlocked server = risk (server room/home only)
- Social engineering against service providers (enable 2FA)

---

## Open Questions for Production

1. **Mac Mini location?** Home office vs. colocation?
2. **Remote access?** VPN, SSH, Tailscale?
3. **Backup strategy?** Time Machine, cloud backup, both?
4. **Monitoring?** Uptime alerts, security alerts, log aggregation?
5. **2FA on services?** Enable where possible (Geopolitical Futures, etc.)

---

## Notes for Future Robert (April 2026)

When you're ready to migrate to server:

1. **Don't panic about keychain not working** - that's expected
2. **Use encrypted .env** - we already built the fallback
3. **Test locally first** - deactivate keychain, test .env mode
4. **Document your server setup** - write it down as you go
5. **Keep dev and prod secrets separate** - different passwords

This document will guide you. You're not starting from scratch - we planned for this.

---

_Last updated: 2026-02-11 by Mini-moi_  
_Revisit: March 2026 (pre-migration testing)_
