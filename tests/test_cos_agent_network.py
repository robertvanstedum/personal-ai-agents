"""CoS's agent network: every member has its own fixed address, and only cos-agent-a holds .2.

On 14 Nov 2026 the first deploy of the network gave model-gateway 192.168.250.2 automatically (it started first), so
cos-agent-a, which is pinned to .2 for the host's metadata rule, failed with "Address already in use", and the portal
and the CoS services that wait on it never started. A member without a fixed address can do that again."""
import ipaddress
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())


def _members():
    out = {}
    for name, svc in COMPOSE["services"].items():
        nets = svc.get("networks") or {}
        if isinstance(nets, list):
            if "cos-agent" in nets:
                out[name] = None
        elif "cos-agent" in nets:
            out[name] = (nets["cos-agent"] or {}).get("ipv4_address")
    return out


def test_every_member_of_the_cos_agent_network_has_a_fixed_distinct_address():
    members = _members()
    assert set(members) == {"cos-agent-a", "model-gateway", "cos-scheduler", "cos-bot"}
    assert all(members.values()), {k: v for k, v in members.items() if not v}
    assert len(set(members.values())) == len(members)
    subnet = ipaddress.ip_network(COMPOSE["networks"]["cos-agent"]["ipam"]["config"][0]["subnet"])
    assert all(ipaddress.ip_address(a) in subnet for a in members.values())


def test_only_cos_agent_a_holds_the_blocked_address():
    blocked = "192.168.250.2"
    assert _members()["cos-agent-a"] == blocked
    assert blocked in (ROOT / "scripts/operations/cos_agent_imds_block.sh").read_text()
