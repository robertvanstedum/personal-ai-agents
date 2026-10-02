"""Container-only synthetic-dev initialization; no application stores mounted."""
import os
from pathlib import Path
import secrets
from .service import DevApplication,DevServer


def main():
    root=Path('/tmp/intelligence-data')
    root.mkdir(mode=0o700,exist_ok=True)
    root.chmod(0o700)
    auth=root/'auth'
    auth.mkdir(mode=0o700,exist_ok=True)
    credential=auth/'owner.token'
    if not credential.exists():
        fd=os.open(credential,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(secrets.token_urlsafe(48)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
    app=DevApplication(root/'vault',credential)
    DevServer(('0.0.0.0',18882),app).serve_forever()


if __name__=='__main__':
    main()
