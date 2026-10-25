"""Observe a local dev rollout by immutable image IDs; print nonsecret JSON only.

No service mutation or publication. The operator supplies exact expected images
from its reviewed deployment package. The requested commit is a request label,
not a claim of CI/source provenance. Root-owned read-only publication is separate.
"""
from pathlib import Path
import argparse
import json
import re
import sys
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.operations.release_status import SERVICES, observation, need
from minimoi_portal.guild_ui.release_context import IMAGE, LOCAL_IMAGE, DEV_CONTAINERS, validated

DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


FORMAT='{"name":{{json .Name}},"container_id":{{json .Id}},"image":{{json .Config.Image}},"image_id":{{json .Image}},"running":{{json .State.Running}},"health":{{with index .State "Health"}}{{json (index . "Status")}}{{else}}null{{end}}}'


def inventory(run=subprocess.run):
    """Fixed dev identities, no inferred aliases and no raw inspect fields."""
    current={s:dict(image=None,image_id=None,running=None,health=None,container_id=None,observation='unavailable') for s in DEV_CONTAINERS}
    try:
        out=run(['docker','inspect','--format',FORMAT,*DEV_CONTAINERS.values()],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.TimeoutExpired):return current
    need(len(out.stdout.encode())<=16384)
    names={v:k for k,v in DEV_CONTAINERS.items()};seen=set()
    for line in out.stdout.splitlines():
        row=json.loads(line);name=row.pop('name','').removeprefix('/')
        need(name in names and name not in seen and set(row)=={'container_id','image','image_id','running','health'})
        seen.add(name)
        row['image']=row['image'] if type(row['image']) is str and LOCAL_IMAGE.fullmatch(row['image']) else None
        current[names[name]]=dict(row,observation='observed')
    # A missing row may be absent or inspection failed. Never turn it into down.
    return current


def detailed_observation(phase,commit,expected,current,previous,now):
    need(phase in ('begin','finish') and type(expected) is dict and expected and set(expected)<=set(DEV_CONTAINERS))
    need(all(type(v) is str and DIGEST.fullmatch(v) for v in expected.values()))
    if previous is not None:
        validated(previous);need(previous['environment']=='dev')
    selected=sorted(expected);changes=[]
    if phase=='finish' and previous and previous['status']=='deployment_in_progress' and previous['requested_tag']==commit and previous['selected']==selected:
        for service,row in current.items():
            before=previous['current'].get(service)
            if before and before['image_id'] is not None and row['image_id'] is not None and before['image_id']!=row['image_id']:
                changes.append(dict(service=service,before=before['image'],after=row['image'],before_image_id=before['image_id'],after_image_id=row['image_id']))
    success=all(current[s]['image_id']==expected[s] and current[s]['running'] is True and current[s]['observation']=='observed' for s in selected)
    doc=dict(format='minimoi.release-observation/2',environment='dev',manifest=None,
        source='local Docker selected-image deployment checks',observed_at=(now or datetime.now(timezone.utc)).isoformat(),
        status='deployment_in_progress' if phase=='begin' else 'selected_images_observed' if success else 'selected_image_check_failed',
        requested_tag=commit,selected=selected,current=current,changes=changes)
    validated(doc);need(len(json.dumps(doc).encode())<=16384)
    return doc


def dev_observation(phase, commit, expected_images, current, previous=None, *, now=None):
    need(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit))
    if type(current) is dict and set(current)==set(DEV_CONTAINERS):
        return detailed_observation(phase,commit,expected_images,current,previous,now)
    need(type(expected_images) is dict and expected_images and set(expected_images) <= set(SERVICES))
    need(all(isinstance(v, str) and DIGEST.fullmatch(v) for v in expected_images.values()))
    need(type(current) is dict and set(current) == set(SERVICES))
    clean = {}
    for name, row in current.items():
        need(type(row) is dict and set(row) == {"image", "image_id", "running", "health"})
        clean[name] = dict(row)
        # Unknown/local tag text is not useful evidence. Keep its immutable image
        # ID, without extending the existing reader's accepted image-name grammar.
        reference = row["image"]
        clean[name]["image"] = reference if isinstance(reference, str) and IMAGE.fullmatch(reference) else None
    if previous is not None:
        validated(previous)
        need(previous["environment"] == "dev")
    selected = sorted(expected_images)
    doc = observation(phase, commit, selected, clean, previous, now=now, environment="dev")
    if phase == "finish":
        matches = all(clean[s]["image_id"] == expected_images[s] and clean[s]["running"] is True for s in selected)
        doc["status"] = "selected_images_observed" if matches else "selected_image_check_failed"
    # No local author text masquerades as a CI manifest; the reader explicitly
    # states that feature changes/acceptance are unverified without that manifest.
    validated(doc)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["begin", "finish"])
    parser.add_argument("commit")
    parser.add_argument("--image", action="append", required=True, metavar="SERVICE=SHA256")
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    expected = {}
    for item in args.image:
        name, sep, value = item.partition("=")
        need(sep and name not in expected)
        expected[name] = value
    previous = None
    if args.previous is not None:
        with args.previous.open("rb") as f:
            raw = f.read(16385)
        need(len(raw) <= 16384)
        previous = json.loads(raw)
    print(json.dumps(dev_observation(args.phase, args.commit, expected, inventory(), previous), sort_keys=True))


if __name__ == "__main__":
    main()
