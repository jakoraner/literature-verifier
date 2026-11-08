---
name: verify-citations
description: Multi-round verification that no citation in a LaTeX project is hallucinated or overstated - metadata, PDF in hand, located passage, adversarial re-check, human sign-off. Use when asked to verify, audit or check citations or references, or before submitting a thesis or paper.
---

# Verify citations

Goal: every citation in the manuscript is backed by a real work, the PDF of that
work in hand, and a located passage (printed page, PDF page, verbatim excerpt)
that supports exactly what the sentence attributes to it. `litverify check`
passing is the definition of done; a human signs off the rest.

## Hard rules (repeat them to every agent you start)

- **R1 the work is real.** Every cited key resolves to a registry record (DOI in
  Crossref or DataCite), or a human has recorded the equivalent record checked.
- **R2 the metadata matches.** Fix the Zotero item, never the .bib by hand.
- **R3 the PDF is in hand.**
- **R4 no verdict without a located passage.** `litverify record` re-reads the PDF
  and refuses an excerpt that is not on the stated page.
- **R5 the source supports exactly the sentence.** Anything less than SUPPORTED
  needs a human decision. **The human decides every PARTLY.**
- **R6 rewording may only narrow a claim, never strengthen it**, and a reworded
  sentence is verified again.
- **R7 the ledger is complete.** **R8 the author has looked.**
- **Never cite from memory.** No agent may add, keep or "fix" a citation from what
  it remembers about a paper.
- Unresolved is fail. When in doubt, the stricter verdict stands.

## Round 0: state

```bash
litverify sync && litverify meta && litverify check; litverify report --json > .litverify/round0.json
```

Read the open items by rule. Tell the user the counts before starting.

## Round 1: metadata and sources

- For R1/R2 items, start one `litverify-scout` agent with the list of keys. It
  reports corrections for Zotero; it does not edit the .bib. Ask the user to apply
  them in Zotero and re-export, then re-run `litverify meta`.
- For R3 items, run `litverify obtain` (and `--download` if an Unpaywall e-mail is
  configured). Give the user the path of `obtain.html`: they borrow, request or
  download what is left. A citation without its PDF cannot pass; do not verify it
  from an abstract or from memory.

## Round 2: independent context checks (parallel)

- Take the R4/R6 items from `litverify report --json`. Group the citation ids by
  source key (one agent reads one PDF once) into batches of about 10 to 25.
- Start one `litverify-verifier` agent per batch, in parallel, each with its
  list of ids and the hard rules. They record with `litverify record --by
  "verifier round 2"`; the ledger is locked per write, so parallel agents are safe.
- When all are done: `litverify check` again.

## Round 3: adversarial re-check

- Take every id recorded in round 2 (at least every SUPPORTED one with a number,
  a direction or a "found/showed" verb, and a random sample of the rest; all of
  them if the project is small).
- Start `litverify-adversary` agents in parallel, batched by source, each writing
  `.litverify/rounds/round3-<n>.json`. Give them the ids, sentences and PDF paths,
  not the recorded excerpts.
- For each disagreement: start a fresh `litverify-verifier` with both passages
  and both reasons. It records the verdict it can support with a located passage.
  If the disagreement remains, record the stricter verdict (PARTLY over
  SUPPORTED) with `--what-differs` naming the disagreement, so the human sees it.

## Repeat

Repeat rounds 2 and 3 on whatever `litverify check` still lists under R4 and R6,
until the only open items are the ones a human must act on: R3 (obtain a PDF), R5
(decide), R8 (sign off). Stop and report if a round makes no progress.

## Final: the human decides

1. `litverify review`, then give the user the path of `litverify-report/review/index.html`.
   They read each sentence beside its passage and page image and answer
   Agree / Disagree / Unsure, export, and run `litverify review --import FILE`.
2. For every PARTLY, WRONG_VERSION, NOT_FOUND or NO_ACCESS: present the sentence,
   the passage and what differs, and a narrowed wording that claims **less** than
   the source. The user decides (narrow the sentence, replace the source, or
   remove the claim) and records it: `litverify decide C0042 "narrowed to ..."`.
   Never run `decide` on the user's behalf without their explicit words.
3. If sentences changed, run the loop again for them (R6).
4. Done when `litverify check --require-human` passes. Report the counts per
   verdict, the decisions taken, and anything still open.
