---
name: mc-corrections
description: Open in the reply where Robert corrects you, or when he says "capture that": the capture block format and what counts as a harness defect.
---

## Correction capture (standing)
Every owner correction is a capture event. In the reply where you take one, or on "capture that", add one fenced block per pair, quoting him verbatim:

```capture
when: <date, conversation>
task: <what you were asked, one line>
did: <what you did, one line>
owner-said: "<Robert's words, verbatim>"
should-have: <the better path, in his words where possible>
tag: scope|role|authorization|priority|evidence|effort|propose-new:<name>
source: robert
```

Then one line: "Captured N correction pairs". If the harness was at fault, write "+1 defect noted" instead. The harness is the platform you run in. Count only its faults: permission loops, missing tools or capabilities, integration failures (example: a tool Robert allowed is refused again and again). A product bug or a model mistake, yours or another agent's, is not a harness defect; don't count it. You never read or write the private judgment store. If judgment cards are loaded, open your first reply of a conversation with "Cards loaded: <name>@<hash>".
