"""The partnership task service (spec v1.1): durable requests, interpretations and tasks, independent of CoS and Records.

Synthetic-first (batch B1). It reuses the single-writer journal pattern of the CoS store (core/durable_files.py), with
the replay index read from the journal under the writer lock so every writer process gives the same answer (B0-1).
"""
