"""The memory shelf's record contract (Memory and retrieval v0.5 §B, amendment v0.5.1 R2/R3).

Pure library, no network and no model. The shelf's files are the record; this
package only defines how a record is named, written, annotated (append-only
events), weighed at read time, and kept in immutable editions.
"""
