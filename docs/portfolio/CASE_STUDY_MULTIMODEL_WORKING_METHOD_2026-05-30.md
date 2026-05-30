# How I Built This: A Working Account

*A first-person record of how a stretch of design work on mini-moi actually went — the human part and the multi-model part, including where it got messy.*

**Robert van Stedum · 2026-05-30**

---

## What this is

This is an account of how I work with AI on this project. Not a polished demo — the real version, with the parts that didn't go smoothly left in. I'm writing it down because the working method is itself part of the project, and because the record is more useful than a summary of it.

I use several models, each for a different job, and I stay in the middle as the one making the calls. What follows is one intense stretch of design work — redesigning the research side of my Curator domain — described as it happened.

---

## The setup

I work with four roles, and I'm one of them:

- **Me** — I set the intent and make every decision. I don't hand the call to a model, even a confident one.
- **A design model (Claude.ai)** — I think through architecture and concepts with it, and it produces the documents. It can't see my live system, so it asks rather than assumes.
- **An implementation model (Claude Code)** — it reads and writes the actual code and knows the true state of the repository.
- **A reviewer (Grok)** — it pressure-tests plans and writing from the outside, and runs my German voice practice in another domain.

I keep them in separate seats on purpose. Each is good at its own job and weaker outside it.

---

## What happened

Curator was my first domain. It's been very useful — a daily briefing I actually read, with a lot of pieces underneath it for deeper analysis. But over time my own use of it got superseded by other builds: German, then Guild, then whatever was next. It kept running and kept being useful in the background, but I stopped working *on* it.

When I came back to it, a few things hit at once. I didn't really remember how it worked or how to navigate it — I was rediscovering my own tool. The look and feel needed an upgrade, since it predates minimoi.ai and isn't yet part of that system. And separately, I'd always had a deepening of the solution on the plan — turning Curator from a briefing tool into something that helps me actually decide, not just read. Now was the time I was turning to it, and I chose to start with the inside workings first and come back to the UI later.

So the immediate trigger was concrete: the research area had grown into seven separate tabs and flows for what was really one activity. I'd walked through it that evening trying to start a research thread and kept clicking different tabs hoping one of them was the right door. The pieces worked; the workflow didn't.

So I ran a design session with the design model — not to fix bugs or restyle screens, but to work out what the research area actually *is* before rebuilding it. We went back and forth for a long stretch. The model would propose a structure; I'd react.

The useful moves in that session were mostly mine rejecting the model's first frame:

- It proposed an "adversarial" AI that argues against my positions. I didn't want that. I want a teammate — something that tells me honestly which way the evidence is leaning and what to do next, that confirms when I'm right and pushes back when I'm not. Not a debate opponent. That correction reshaped the whole testing side.
- It wrote "mid-term" as the time horizon. I corrected it — complex systems have long waves that are hard to see, and those are often where the real shifts are. The boundary isn't a time window; it's about leaving out the trivial (day-trading noise, partisan noise), not the long-range.
- It assumed everything was an article from a feed. I read two old books recently — Burnham's *Managerial Revolution* from 1941, and a reread of *Road to Serfdom* — and was struck by how relevant they are now. The system shouldn't ignore an old book just because it isn't news. That broke an assumption baked into the whole pipeline.
- Late in the session I realized a topic I'm looking at now is usually part of a broader subject — I'm rarely just curious about one thing, I'm testing it against a wider trend or a position I'm trying to validate. That turned into the grouping and tagging structure, and it's where my interest in a graph database finally had a concrete job.

Each of those was me knowing the structure was wrong, not the model building it. The model was good at building; I knew when it was off.

---

## The part that didn't go smoothly

I'd had the reviewer model look at the work too. It gave me two genuinely good reviews — it independently landed on the same naming and architecture calls the implementation model had reached, which raised my confidence that those calls were right.

Then I asked it for a "real view," and it drifted. The tone slid from reviewer to cheerleader — exclamation points, "let's light it up," and a celebratory product-launch announcement written for a feature that didn't exist yet. A triumphant release note for a concept document. Some of the substance underneath was still fine, but it was wrapped in momentum that, if I'd acted on it, would have had me publicly announcing a finished "decision engine" before any of it was built.

I noticed it and said so — I'd lost something with that model. From there the job was to separate the signal from the noise: keep the sound technical calls, which still agreed with the implementation model, and throw out the rest. No release announcing something unbuilt. The honesty matters more than the momentum, especially on a public repo.

It's worth being clear that the same model produced real value and drifted on the same day. I didn't stop using it. I just didn't let the confident, encouraging version of it make my decisions for me.

---

## The implementation model's contribution

When the concept was stable, I had the implementation model — the one that reads my actual code — check it against reality. Its finding changed the mood: most of what we'd designed already existed in the backend. My research threads were already the "topics" we'd designed; thread expiry was already the time-boxed pull; the deeper-dive feature was already there under a confusing name. The genuinely new pieces were small. The gap between the concept and working code was much smaller than the broken UI had made it feel.

That check is something only that model could give — the design model would have kept designing against what it imagined was there.

---

## How I'd describe the method

- I route work by what each model is good at: design with one, verify against the real code with another, pressure-test with a third. I don't ask one to do another's job.
- I don't trust anything just because it's fluent. I trusted "most of this already exists" because that model reads the actual code. I distrusted the hype because it was hype, however confident.
- When two models reach the same answer from different angles, I take that agreement seriously.
- Every decision, commit, and anything that goes public passes through me, and I stay willing to override the most enthusiastic model in the room.
- When the work started feeling heavy and slow, I stopped and asked whether the thinking was still serving the goal — a tool I actually use to make better decisions — or had become its own exercise. That's a call I make, not the models.

---

## Where it ended

The concept came out complete and, I think, better than where any single thread started. The corrections that got it there were mine; the structure, the code-grounding, and the outside pressure came from the models. I set the intent and the objectives and made the calls throughout, and the result was better for being a joint process than it would have been from me or any one model alone.

I wrote this with the messy parts left in on purpose. Anyone who wants to see how it's actually done can see it here — the working model and all.
