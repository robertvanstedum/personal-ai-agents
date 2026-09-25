"""Minimal agent adapter: authenticated reads/writes, never launches a model."""
import argparse
import base64
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
from uuid import uuid4


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:18880")
    parser.add_argument("--token-file",required=True,help="Private client credential file; never pass tokens in room text")
    parser.add_argument("--operation-id",help="Reuse the same ID and payload after an uncertain response")
    commands=parser.add_subparsers(dest="command",required=True)
    commands.add_parser("rooms")
    commands.add_parser("inbox")
    coordination=commands.add_parser("coordination");coordination.add_argument("room")
    request=commands.add_parser("request");request.add_argument("room");request.add_argument("path",help="JSON kind/title/body/assignee payload")
    transition=commands.add_parser("respond");transition.add_argument("room");transition.add_argument("item");transition.add_argument("path",help="JSON action/body/version payload")
    receipt=commands.add_parser("receipt");receipt.add_argument("room");receipt.add_argument("operation")
    read=commands.add_parser("read");read.add_argument("room")
    search=commands.add_parser("search");search.add_argument("query")
    post=commands.add_parser("post");post.add_argument("room");post.add_argument("--text",required=True)
    post.add_argument("--kind",default="message",choices=["message","checkpoint","proposal","task","task_update"])
    post.add_argument("--target");post.add_argument("--reference")
    post.add_argument("--context-class",choices=["robert_source","external_source","agent_draft","coauthored_output"])
    transfer=commands.add_parser("transfer");transfer.add_argument("room");transfer.add_argument("grant_id")
    imp=commands.add_parser("import");imp.add_argument("room");imp.add_argument("path",help="Exact JSON payload, retain for retries")
    upload=commands.add_parser("file");upload.add_argument("room");upload.add_argument("path");upload.add_argument("--source",required=True)
    upload.add_argument("--context-class",choices=["robert_source","external_source","agent_draft","coauthored_output"])
    link=commands.add_parser("link");link.add_argument("room");link.add_argument("--event-id",required=True)
    link.add_argument("--kind",required=True,choices=["sha256","git_commit","spec_path","work_artifact"])
    link.add_argument("--value",required=True);link.add_argument("--revision",required=True);link.add_argument("--label",required=True)
    args=parser.parse_args()
    origin=urlsplit(args.url)
    if origin.scheme!="http" or origin.hostname not in {"127.0.0.1","localhost"} or origin.username or origin.password or origin.path not in {"","/"} or origin.query or origin.fragment:
        parser.error("This proof adapter only contacts an explicit local HTTP service")
    credential=Path(args.token_file)
    if credential.is_symlink() or credential.stat().st_mode&0o077 or credential.stat().st_uid!=__import__("os").getuid():
        parser.error("Credential file must be owner-private and not a symlink")
    token=credential.read_text().strip()
    data=None
    if args.command=="rooms": path="/api/v1/rooms"
    elif args.command=="inbox": path="/api/v1/coordination/inbox"
    elif args.command=="coordination": path=f"/api/v1/rooms/{quote(args.room,safe='')}/coordination"
    elif args.command in {"request","respond"}:
        if not args.operation_id:parser.error("Coordination writes require a retained --operation-id")
        file=Path(args.path)
        if file.stat().st_size>32_000:parser.error("Coordination payload exceeds32KB")
        data=json.loads(file.read_text())
        if not isinstance(data,dict):parser.error("Payload must be a JSON object")
        path=f"/api/v1/rooms/{quote(args.room,safe='')}/coordination"
        if args.command=="respond":path+="/"+quote(args.item,safe='')
    elif args.command=="receipt": path=f"/api/v1/operations/{quote(args.operation,safe='')}?destination={quote(args.room,safe='')}"
    elif args.command=="read": path=f"/api/v1/rooms/{quote(args.room,safe='')}"
    elif args.command=="search": path="/api/v1/search?q="+quote(args.query,safe="")
    elif args.command=="transfer":
        if not args.operation_id: parser.error("Transfer requires a retained --operation-id")
        path=f"/api/v1/rooms/{quote(args.room,safe='')}/transfers"
        data=dict(grant_id=args.grant_id)
    elif args.command=="import":
        if not args.operation_id: parser.error("Import requires a retained --operation-id")
        file=Path(args.path)
        if file.stat().st_size>2_000_000: parser.error("Import is larger than 2 MB")
        data=json.loads(file.read_text())
        if not isinstance(data,dict): parser.error("Import must be a JSON object")
        path=f"/api/v1/rooms/{quote(args.room,safe='')}/imports"
    elif args.command=="post":
        path=f"/api/v1/rooms/{quote(args.room,safe='')}/events"
        data=dict(body=args.text,kind=args.kind,target=args.target,reference=args.reference,context_class=args.context_class)
    elif args.command=="link":
        path=f"/api/v1/rooms/{quote(args.room,safe='')}/artifact-refs"
        data=dict(event_id=args.event_id,kind=args.kind,value=args.value,revision=args.revision,label=args.label)
    else:
        file=Path(args.path)
        if file.stat().st_size>2_000_000: parser.error("File is larger than 2 MB")
        path=f"/api/v1/rooms/{quote(args.room,safe='')}/documents"
        data=dict(name=file.name,source_note=args.source,base64=base64.b64encode(file.read_bytes()).decode(),context_class=args.context_class)
    operation=args.operation_id or str(uuid4())
    headers={"Authorization":f"Bearer {token}"}
    if data is not None:
        headers.update({"Content-Type":"application/json","Idempotency-Key":operation})
        print(f"Operation ID (retain for safe retry): {operation}",file=__import__('sys').stderr)
    req=Request(args.url.rstrip("/")+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
    try:
        with build_opener(NoRedirect).open(req,timeout=15) as response: result=json.load(response)
        print(json.dumps(result,indent=2,ensure_ascii=False))
    except HTTPError as error:
        print(error.read().decode(),file=__import__('sys').stderr)
        raise SystemExit(1)


if __name__=="__main__":main()
