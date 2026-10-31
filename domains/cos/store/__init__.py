"""The CoS store: goals, plans, obligations and CoS's own decisions, behind one command writer.

Spec v0.3 section 4.5 (planning-studio INIT-2026-0007, 15_SPEC_1_2_COS_FIRST_v0.3). The store lives at
/opt/minimoi/cos/store on production and is written only by the CoS backend (minimoi-cos-scheduler). Every other
process sends commands. The frozen contracts are contracts/cos/v1; every command is validated against them.
"""
from domains.cos.store.writer import StoreWriter, StoreRefused  # noqa: F401
