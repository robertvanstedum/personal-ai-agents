# Partnership specification v1.1 addendum 1

**4 November 2026 · Normative clarification of Q01, Q04, Q06 and L04 · Application edition**

This addendum supplements specification v1.1 without replacing its pinned bytes or changing existing requirement IDs. It records the adopted dispositions of the closing accountability review. Work already in progress continues; the implementer records this addendum's hash and dispositions at the next package checkpoint. No restart, new blanket approval gate or execution authority is introduced. Unaffected v1.1 requirements remain in force.

## Consequential operations and lightweight history

**AC01.** The full Q01 evidence chain applies to operations that change another seat's obligations, write outside the responding seat's own turn history, reserve or spend money, or produce an external effect. Ordinary answers and lookups retain D03 source, coverage and result history without artificial task promotion. Lightweight history MUST NOT bypass authorization, disclosure controls or Q04 spending evidence: a paid lookup or paid model attempt remains attributable to its request and spending authority even when no task is needed. B0/B1 MUST document this classification once and include representative paid and unpaid lookup cases. Scope follows actual effects and spending, not the tool's label or whether the action is called a read.

## Checkpoint coverage and detection limits

**AC02.** The Q06 trust map MUST state the intended independent-checkpoint cadence, the last verified independently retained checkpoint and its coverage, and the current unanchored interval. A proposed nightly cadence or approximately 24-hour interval is a live-use assumption until actual independent custody, successful checkpoint creation and verification are evidenced. A missed checkpoint MUST expose a widening coverage gap; it MUST NOT continue reporting the intended cadence as achieved protection.

Checkpoint cadence alone does not guarantee a maximum time to detect tampering. State verification frequency/delay and the threat assumptions separately. A checkpoint can expose later alteration of covered history when compared with trustworthy retained evidence; it cannot prove that events were truthful when first recorded or detect every rewrite before anchoring. Co-located recomputable hashes alone do not resist privileged rewriting. Report these limits without claiming a bound the deployed mechanism cannot establish. Synthetic fixtures verify missed-checkpoint and integrity-gap reporting; live cadence and custody remain separately evidenced.

## Eighth proposed Practice Book seed

**AC03.** Add the proposed template: **“Answer ‘what happened’ from the record; label inference as inference.”** In conversational incident explanations, distinguish authenticated observations, actor reports and hypotheses, cite accessible evidence, and state material missing coverage. An explanation MUST NOT invent a causal conclusion from an assignment, receipt or absent record. This extends Q07 to spoken and written explanations without requiring hidden model reasoning.

The eighth seed is proposed template content under L04, not an adopted live practice. Live adoption, scope, edition history and loading receipts follow L01–L04. It grants no authority and does not silently modify the seven existing templates.

## Build disposition

B0/B1 records AC01 classification and AC02 threat-map assumptions. Include AC03 as a proposed seed fixture; B3 retains responsibility for governed live adoption/loading machinery. At the next package review, the implementer reports satisfied, pending or conflicting for each AC requirement with evidence. Existing package boundaries and separate live-release gates remain unchanged. The addendum is documentation, not evidence that any implementation or test has passed.
