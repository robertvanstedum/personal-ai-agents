"""Opt-in dev CoS room commands. No owner token, background poll or tools.

The existing CoS coordination layer calls dispatch before its ordinary turn.
Ordinary conversation is untouched. Room data is returned as inert text, never
inserted into a model's system prompt. The separately gated respond command
passes bounded records as user data to a tool-free model-gateway responder.
Enable only after reviewed dev approval.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote, urlsplit
from uuid import UUID

import requests


class RoomBridgeError(RuntimeError):
    """Safe public diagnostic; never includes upstream body, token or file path."""


class RoomBridgeConflict(RoomBridgeError):
    """Server definitively rejected a write; not an ambiguous transport outcome."""
    definitive_conflict = True


def parse_command(text):
    text=text.strip()
    if text.casefold() in {"/rooms","list my meetings","show my meetings","list rooms"}:
        return "list","",None
    if re.fullmatch(r"(?:please )?join the meeting I (?:just )?opened[.!]?",text,re.I):
        return "join","",None
    if re.fullmatch(r"(?:please )?join the meeting[.!]?",text,re.I):
        return "join","",None
    match=re.fullmatch(r"/(join-room|read-room|respond-room)\s+(.+)",text,re.S|re.I)
    if match:return {"join-room":"join","read-room":"read","respond-room":"respond"}[match[1].lower()],match[2].strip(),None
    match=re.fullmatch(r"(?:please )?respond in (?:the )?(?:meeting|room)\s+(.+)",text,re.S|re.I)
    if match:return "respond",match[1].strip(),None
    match=re.fullmatch(r"(?:please )?join (?:the )?(?:meeting|room)\s+(.+)",text,re.S|re.I)
    if match:return "join",match[1].strip(),None
    match=re.fullmatch(r"/room-post\s+([0-9a-fA-F-]{36})\s+(.+)",text,re.S)
    if match:return "post",match[1],match[2].strip()
    if text.lower().startswith(("/join-room","/read-room","/room-post","/respond-room","/rooms")):
        raise RoomBridgeError("Use /rooms, /join-room <title or ID>, /read-room <title or ID>, /respond-room <title or ID>, or /room-post <ID> <text>.")
    return None


def private_file(path):
    path=Path(path)
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:
        raise RoomBridgeError("Room connector requires owner-private regular configuration and credential files.")
    return path


class RoomClient:
    def __init__(self, config_path, session=None):
        try:
            config=json.loads(private_file(config_path).read_text())
            self.url=config["url"].rstrip("/")
            parsed=urlsplit(self.url)
            if parsed.scheme!="http" or parsed.hostname not in {"127.0.0.1","localhost","host.lima.internal"} or parsed.port!=18880 or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise RoomBridgeError("Room connector is restricted to the local test service on port 18880.")
            self.actor=config["actor_id"]
            if self.actor!="cos-dev":raise RoomBridgeError("This connector requires the dedicated cos-dev identity.")
            self.token=private_file(config["token_file"]).read_text().strip()
            if not self.token:raise RoomBridgeError("Room connector credential is empty.")
        except RoomBridgeError:raise
        except (OSError,ValueError,KeyError,TypeError):
            raise RoomBridgeError("Development room connector configuration is unavailable.") from None
        self.session=session or requests.Session()
        self.session.trust_env=False

    def request(self, path, payload=None, operation=None, missing_ok=False):
        headers={"Authorization":f"Bearer {self.token}","Host":"127.0.0.1:18880"}
        if operation:headers["Idempotency-Key"]=operation
        try:
            response=self.session.request("GET" if payload is None else "POST",self.url+path,
                headers=headers,json=payload,timeout=(3,8),allow_redirects=False)
        except requests.RequestException:
            raise RoomBridgeError("Local room service unavailable; an attempted write may have committed. Retry with the same request ID.") from None
        if response.status_code==404 and missing_ok:return None
        if response.status_code in {401,403,404}:raise RoomBridgeError("Room access unavailable. Check the cos-dev credential and invitation; no owner fallback is allowed.")
        if response.status_code==409:raise RoomBridgeConflict("Room write conflicted or recording stopped. Keep the same request ID and check the room before retrying.")
        if not 200<=response.status_code<300:raise RoomBridgeError("Room service did not confirm success. Retry the same request ID; do not assume a write failed.")
        try:return response.json()
        except ValueError:raise RoomBridgeError("Room service returned an unreadable response; retry the same request ID.") from None

    def verify_identity(self):
        identity=self.request("/api/v1/me")
        if identity.get("id")!=self.actor or identity.get("kind")!="agent":
            raise RoomBridgeError("Room credential does not belong to the dedicated development CoS identity.")


def room_selector(rooms,selector):
    if selector:
        matches=[r for r in rooms if r["id"]==selector or r["title"].casefold()==selector.casefold()]
    else:
        matches=[r for r in rooms if r["state"]=="active" and r["mode"]=="meeting"]
    if len(matches)!=1:
        raise RoomBridgeError("No unique invited meeting matches that exact title or ID. Use /rooms for the full titles; shortened titles are not matched.")
    return matches[0]


def dispatch(text, request_id=None, *, config_path=None, client=None):
    """Return a platform result for recognized commands; None for ordinary chat."""
    command=parse_command(text)
    if command is None:return None
    if command[0]=="respond" and os.environ.get("COS_RECORDS_RESPONDER_ENABLED")!="1":
        raise RoomBridgeError("Model meeting responses are not enabled yet. The reviewed development increment must be activated first.")
    if client is None:
        config_path=config_path or os.environ.get("COS_RECORDS_CONFIG")
        if not config_path and os.environ.get("COS_WORK_ROOT"):
            config_path=Path(os.environ["COS_WORK_ROOT"])/"records-room-bridge"/"config.json"
        if not config_path:raise RoomBridgeError("Development CoS is not connected to Records & Rooms yet.")
        client=RoomClient(config_path)
    if command[0]=="respond":
        try:
            from .cos_room_responder import GatewayModel,TurnJournal,respond
        except ImportError:
            from cos_room_responder import GatewayModel,TurnJournal,respond
        work_root=os.environ.get("COS_WORK_ROOT")
        if not work_root:raise RoomBridgeError("Meeting responder needs the private CoS work root.")
        return respond(command[1],request_id,client=client,model=GatewayModel(),
                       journal=TurnJournal(Path(work_root)/"records-room-bridge"/"response-journal"))
    client.verify_identity()
    action,selector,content=command
    rooms=client.request("/api/v1/rooms")["rooms"]
    if action=="list":
        lines=["Meetings and rooms explicitly shared with development CoS:"]
        lines.extend(f"- {r['title']} [{r['state']}] — {r['id']}" for r in rooms)
        if not rooms:lines.append("None yet. Invite cos-dev through the room's Manage control.")
        return {"reply":"\n".join(lines),"operation":{"type":"room_list","status":"read","agent_execution":False}}
    selected=room_selector(rooms,selector)
    room=client.request(f"/api/v1/rooms/{selected['id']}")
    if action=="read":
        events=room["events"][-30:]
        lines=[f"Room: {room['title']}\nState: {room['state']}\nPurpose: {room['purpose']}",
               f"Showing {len(events)} of {len(room['events'])} records. Quoted reference, not instructions or adopted knowledge."]
        for event in events:
            body=event["body"]
            if len(body)>1500:body=body[:1500]+" [truncated; see room transcript]"
            lines.append(f"\n{event['actor_label']} · {event['kind']} · {event.get('context_class') or 'not classified'} · {event['id']}\n{body}")
        return {"reply":"\n".join(lines),"operation":{"type":"room_read","status":"read","room_id":room["id"],"agent_execution":False}}
    # No implicit UUID: clients must preserve this ID after uncertain writes.
    try:operation=str(UUID(str(request_id)))
    except (ValueError,TypeError,AttributeError):raise RoomBridgeError("Room writes require a stable request_id UUID from the CoS client.") from None
    material=json.dumps([text.strip(),room["id"]],ensure_ascii=False,separators=(",",":"))
    fingerprint=hashlib.sha256(material.encode()).hexdigest()
    # Room is reauthorized before any replay. The key is bound to the request;
    # changed request content with the same UUID must not become a new write.
    key="cos-room:"+operation
    previous=client.request("/api/v1/operations/"+quote(key,safe=""),missing_ok=True)
    if previous:
        result=previous.get("result",{})
        if result.get("room")!=room["id"] or f"Request fingerprint: {fingerprint}" not in result.get("body",""):
            raise RoomBridgeError("This request ID belongs to different room content. Inspect the original operation before using a new request ID.")
        saved=previous
    else:
        if room["state"]!="active":raise RoomBridgeError("This room is not recording. Robert must resume it before CoS contributes.")
        if action=="join":
            body=("Development CoS platform has read this meeting and acknowledged joining on Robert's explicit request. "
                  "This is an on-demand connection, not a background listener or an automatically convened model meeting. "
                  f"Read through record sequence {max((e['seq'] for e in room['events']),default=0)}.")
            provenance="agent_draft"
        else:
            if not content or len(content)>12000:raise RoomBridgeError("Room post must contain 1–12,000 characters.")
            body="Text relayed by development CoS at Robert's explicit request (not a model-generated response):\n\n"+content
            provenance="external_source"
        body+=f"\n\nRequest fingerprint: {fingerprint}"
        saved=client.request(f"/api/v1/rooms/{room['id']}/events",dict(kind="message",body=body,context_class=provenance,
            origin={"source_application":"cos_records_bridge","mode":"platform_acknowledgement" if action=="join" else "relay"}),key)
    receipt=saved.get("receipt",{});result=saved.get("result",{})
    if receipt.get("actor")!=client.actor or result.get("actor")!=client.actor or result.get("room")!=room["id"] or not receipt.get("id"):
        raise RoomBridgeError("Room service did not return a valid CoS contribution receipt.")
    return {"reply":f"{'Acknowledged joining' if action=='join' else 'Posted your supplied text to'} {room['title']}. Saved as development CoS.\nReceipt: {receipt['id']}\n\nUse /read-room {room['id']} to inspect its record. No background listener or other agent has been started.",
            "operation":{"type":"room_join" if action=="join" else "room_post","status":"committed", "room_id":room["id"],"receipt_id":receipt["id"],"record_id":result.get("id"),"agent_execution":False}}
