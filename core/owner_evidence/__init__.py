"""The owner-evidence suite (MC front door spec §8): the standing acceptance instrument for MC and CoS.

Cases come from Robert's real interactions, so the corpus lives in the private planning repository and is passed by path. This
package holds only the engine. Each case runs in three modes:

* cold: memory withheld. Fresh judgement and voice. Inventing history fails harder than missing it.
* warm: memory included. It must recognise the earlier decision; treating a known request as new is a memory failure.
* variant: the case changed. It must think in the context of the earlier decision, and must not open a second item or repeat
  the old decision unchanged.

Two things are graded separately. Hard rules (the right tool or none, nothing forbidden) are deterministic here. Judgement and
personality are graded by Robert against a short rubric, by sampling transcripts; never by an agent's own rating. A persona
release is blocked until every trial passes the hard rules and he has passed the rubric, on one frozen bundle.
"""
from .suite import MODES, Bundle, Case, Grade, Run, Suite, gate, grade, load, run, sample_sheet  # noqa: F401
