# Guild 1.1 on dev: wall of screens, 2026-09-30

**File:** `Guild_1_1_dev_screens_2026-09-30.pdf`. It has 36 pages: a cover, then one captioned page per scene.

**Code captured:** `staging/integration-2026-09-29` at `5c57bdc94f34432e8aab50694d45d742456ebe28`. That is the tree dev.minimoi.ai runs. It holds #261, #263, #265, #266, #267, #270/#274, #271, #275, #276 and #277.

**Sample data from a local test instance, not production.** Nobody signed in to dev.minimoi.ai or production, and no model was called:

- **How it was served.** The pages come from the shared tour_capture tool's new local sample server (`python -m scripts.tools.tour_capture.local_sample`). That server runs the integration portal on 127.0.0.1 with the Shop floor harness's sample queue and floor store.
- **Master Craftsman.** MC answers from a scripted relay.
- **CoS.** CoS goes through its real `/app/cos` proxy to a stand-in cos-scheduler that echoes "Noted: …", plus a stand-in voice adapter (no microphone, no provider).
- **Browser.** The browser could reach loopback only, so web fonts fall back to system fonts.

**Scenarios:** `scripts/tools/tour_capture/scenarios/guild_1_1_{desktop,narrow,tablet,phone}.json`.

**PNG runs:** `_working/guild-1-1-capture/run-20260929-210608/guild-1-1-*/<UTC>/` (raw PNGs, WebPs, manifest.json, report.json and review.html).

## Pages

The PDF groups pages by device (one tour_capture run per viewport): desktop 1440×900, then narrow 1100×800, tablet 860×800 and phone 390×844.

| Page | Device | Scene | Brief item |
|---|---|---|---|
| 2 | desktop | The /guild landing (Build card as a shared partial) | 1 |
| 3 | desktop | Shop floor: formatted MC reply (headings, list, code, table), note right, Needs-you badge, rail | 2 |
| 4 | desktop | Shop floor: the (i) fold open | 2 |
| 5 | desktop | Rail quiet ("Nothing needs you right now · Open wall") | 3 |
| 6 | desktop | Stale read (dashed "?" badge, no quiet line) | 3 |
| 7 | desktop | Streaming mid-reply ("Writing… 1s", Stop) | 4 |
| 8 | desktop | Finished reply with "Done in Ns · N output tokens" | 4 |
| 9 | desktop | Stopped reply and its honest line | 4 |
| 10 | desktop | Blocker line: MC not answering | 5 |
| 11 | desktop | Blocker line: last answer failed | 5 |
| 12 | desktop | Conversations: pinned on top, row menu open, "+ New conversation" | 6 |
| 13 | desktop | Conversations: the Archive view | 6 |
| 14 | desktop | Off the record: on | 7 |
| 15 | desktop | Off the record: "record mode unknown" hold | 7 |
| 16 | desktop | The wall with MC open (floating, with Dock) | 9 |
| 17 | desktop | Queue | 10 |
| 18 | desktop | An item page | 10 |
| 19 | desktop | "Start a conversation about this" | 10 |
| 20 | desktop | A conversation about #12 | 10 |
| 21 | desktop | Workshop from a queue item (host "tight", named limits, sessions on this Mac) | 11 |
| 22 | desktop | Workshop: refresh failed ("last good read") | 11 |
| 23 | desktop | Operate | 12 |
| 24 | desktop | Labs | 12 |
| 25 | desktop | CoS Confer: text reply | 14 |
| 26 | desktop | CoS Confer: voice controls (start in progress) | 14 |
| 27 | desktop | CoS Confer: voice did not start | 14 |
| 28 | narrow 1100 | Rail toggle open | 8 |
| 29 | tablet 860 | History drawer open | 8 |
| 30 | phone | The /guild landing | 1 |
| 31 | phone | Shop floor: chat first, Build strip header | 13 |
| 32 | phone | Shop floor: context expanded | 13 |
| 33 | phone | Shop floor: history drawer | 13 |
| 34 | phone | The wall (one column, filters) | 9 |
| 35 | phone | Workshop (Now / Needs you / Budget first) | 11 |
| 36 | phone | CoS Confer: text reply | 14 |

## Skipped or partial

- **Voice Phase A is not on the branch.** That covers the greeting, the live transcript and the Speak and write / Write only toggle. Pages 26–27 show the voice controls that are there. Re-run `guild-1-1-desktop` when Phase A lands.
- **Streaming shows one in-progress state.** The page shows "Writing… 1s" with text already arriving. The earlier "Working…" state, before the first text, was not captured on its own.
- **The desktop Workshop is a viewport capture, not a full page.** It fits in 1440×900.
- **On the wall, MC is shown as it opens: floating.** The docked state (after pressing Dock) was not captured.

## What looked broken

These were seen with the stand-in relay and sample data. Confirm on dev before filing.

1. **The blocker line contradicts the header.** After a good reply, the header says "Master Craftsman is live", but the blocker line above the composer still says "unavailable · connected, no answer yet" (pages 3, 8 and 31). While a reply streams, the header also flips to that text (page 7). It looks like the first answer after a portal start: the cached health is not refreshed by the answer.
2. **Stop is reported as a failure.** After Stop, the blocker adds "Last answer failed · …" (page 9). When MC fails, the same failure shows twice: once from the server's blocker and once as "Last answer failed" (page 10).
3. **Off the record barely changes the page.** The header over the chat still reads "your messages are kept as notes" (page 14). The only signals are a thin band and the button label. The mode has no colour or shape of its own.
4. **The (i) help text is stale.** It says MC "has not answered yet on this portal" even when MC is live and has just answered (page 4).
5. **Phone layout problems:**
   - A large "Type" button sits under a composer that is already open on the Shop floor (pages 31–32), and it is pinned over the wall and the Workshop (pages 34–35).
   - The portal bar is cut: "Meu Portuguê" is clipped and CoS is off-screen. The Guild subnav is clipped at "PL…".
6. **The Queue page leaves out what the floor calls most urgent.** The Queue shows only Spec ready and In build (page 17), so blocked #31 has no place there. Times are also shown in UTC on the Queue, item page and wall ("read 02:11 UTC") but in local time on the floor ("09:11 PM").
7. **The floating MC panel covers the wall's content.** On the desktop wall, MC opens as a floating panel (with a Dock button). It covers the lower right of Post-its and half of the Blocked panel (page 16), and its context line still says "workbench".
8. **Naming drifts.** The nav says "Wall", but the Queue's back button, the item page and the wall's own subtitle say "Workbench".

## Where the Shop floor, the wall and the Workshop look the same

This is for the Codex handoff on Robert's "all the same, essentially".

- **One skin everywhere.** All three pages, plus Queue and Operate, share the same tan page and dark subnav. They use the same cream cards with small uppercase letter-spaced headings, the same body size and the same link style. Nothing at the top of the page says which room you are in, apart from the active subnav word and a serif H1 on the wall and the Workshop.
- **"Needs you" appears in three look-alike forms.** It is a pill badge on the floor, a card on the wall and a card on the Workshop (pages 3, 16 and 21). All three are cream boxes with the same heading. The Workshop's version is agent-scoped ("CLAUDE-CODE · Choose the refresh cadence"), but it reads the same as the wall's.
- **The floor rail is a small copy of the wall.** Most urgent, Continue ("Last opened") and Open wall repeat the wall's Needs you and Continue panels. The rail's Build card image is the same one the landing uses.
- **The Workshop and Operate are both grids of three or four cream cards** (pages 21 and 23). The Workshop's host verdict ("HOST TIGHT") is a small outlined pill, the same weight as the Operate light words. It is not a dominant gauge.
- **A floating "Conversation · Master Craftsman off" pill sits bottom-right** on the Queue, item, Workshop, Operate and Labs pages. On the wall it opens as a docked panel, and on the floor it is the whole page. This makes the other pages feel like the same app state with different cards.
- **Suggestion:**
  - Give each room one dominant element and its own accent: the floor is the conversation, the wall is a board of post-its and needs, and the Workshop is a host and agents gauge.
  - Put a room header with a clear title on every page.
  - Keep "Needs you" in one canonical shape, linked from the others.
