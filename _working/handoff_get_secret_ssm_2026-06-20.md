# Handoff — Update get_secret() for AWS SSM Parameter Store
*Phase 1 Step 11 — Claude Code task*
*Date: 2026-06-20*

## What to do

Update `get_secret()` in all three Flask apps to read from AWS SSM
Parameter Store in production, with local `.env` fallback for dev.

## Find the current get_secret() function

Search the codebase:
```bash
grep -rn "get_secret\|def get_secret" . --include="*.py"
```

This will show where it currently lives in each app.

## Replace with this implementation

```python
import os
import boto3

def get_secret(key: str) -> str:
    """
    Read a secret from environment variable (local dev)
    or AWS SSM Parameter Store (production).
    
    SSM path convention: /minimoi/production/{key.lower()}
    """
    # Local dev: use environment variables first
    env_val = os.environ.get(key)
    if env_val:
        return env_val

    # Production: read from SSM Parameter Store
    try:
        ssm = boto3.client('ssm', region_name='us-east-1')
        param_name = f"/minimoi/production/{key.lower()}"
        response = ssm.get_parameter(
            Name=param_name,
            WithDecryption=True
        )
        return response['Parameter']['Value']
    except Exception as e:
        raise RuntimeError(
            f"Could not retrieve secret '{key}' from SSM "
            f"(path: /minimoi/production/{key.lower()}): {e}"
        )
```

## Add boto3 to requirements

Add `boto3` to requirements.txt in all three apps if not already present:
```bash
grep -r "boto3" . --include="requirements*.txt"
```

If missing, add: `boto3>=1.34.0`

## SSM parameter names (already created in AWS)

| Secret key name | SSM path |
|----------------|----------|
| OPENAI_API_KEY | /minimoi/production/openai_api_key |
| ANTHROPIC_API_KEY | /minimoi/production/anthropic_api_key |
| GROK_API_KEY | /minimoi/production/grok_api_key |
| FLASK_SECRET_KEY | /minimoi/production/flask_secret_key |

## Definition of Done

- get_secret() updated in all three apps (portal, curator, german)
- boto3 in all three requirements.txt files
- Local dev still works via .env (fallback preserved)
- No hardcoded keys anywhere

## Commit message

`Update get_secret() to read from AWS SSM Parameter Store with local env fallback`
