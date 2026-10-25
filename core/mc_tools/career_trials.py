"""Code-owned synthetic recipe, exact canonical artifact hashes; no model inputs."""
import json
from pathlib import Path

def prepared():
    manifest=json.loads(Path(__file__).with_name('career_trial_manifest.json').read_text())
    payload=json.dumps({'career_trial':manifest},sort_keys=True,separators=(',',':')).encode()
    return payload,{'fixture':'synthetic-v1','kit_commit':manifest['kit_commit'],
        'recipe_sha256':manifest['recipe_sha256'],'page_count':'not checked; no renderer invoked',
        'source':'fixed synthetic fixture; no owner data or model inference',
        'verification':'exact canonical artifact hashes; fixed synthetic checks',
        'check_names':manifest['check_names']}
