"""Fetch the pinned Codex CLI for the rooms-codex image and verify it
(npm tarball SHA-512, then the binary's SHA-256). Writes /codex only."""
import base64
import hashlib
import io
import sys
import tarfile
import urllib.request

version, tgz_sha512, bin_sha256 = sys.argv[1:4]
url = f"https://registry.npmjs.org/@openai/codex/-/codex-{version}-linux-arm64.tgz"
data = urllib.request.urlopen(url, timeout=300).read()
if base64.b64encode(hashlib.sha512(data).digest()).decode() != tgz_sha512:
    sys.exit("tarball integrity mismatch")
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    binary = archive.extractfile("package/vendor/aarch64-unknown-linux-musl/bin/codex").read()
if hashlib.sha256(binary).hexdigest() != bin_sha256:
    sys.exit("binary hash mismatch")
with open("/codex", "wb") as out:
    out.write(binary)
