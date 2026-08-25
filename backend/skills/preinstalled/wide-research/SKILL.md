---
name: wide-research
description: >
  Builds comprehensive, evidence-backed lists or datasets by partitioning many
  independent research items across parallel subagents, then validating,
  deduplicating, and gap-filling the combined results. Use for requests to find
  all or many matching entities, map a market or literature set, enrich a known
  list, or apply the same research contract across many inputs; use
  deep-research instead for one topic that needs multi-step investigation.
version: 1.1.0
keywords:
  - research
  - wide-research
  - broad-information-seeking
  - multi-agent
  - parallel-research
  - dataset-building
  - exhaustive-search
  - market-map
---

# Wide Research

Build a verifiable set of rows, not a collection of mini-reports. The main agent
owns the universe definition, source strategy, row contract, partition manifest,
canonical ledger, validation, coverage assessment, and final dataset. Subagents
only execute bounded, independent slices using that shared design.

Use this workflow when breadth is the hard part: enumerating many entities,
enriching a known list, traversing a registry, or building a market map. Use
`deep-research` when one subject needs a chain of discoveries or causal analysis.
Use direct search for one quick fact.

## Non-negotiable invariants

1. Define the universe, row schema, and evidence threshold before large fan-out.
2. Select and test shared sources before splitting collection work.
3. Give every input or partition one owner and preserve every terminal state.
4. Separate discovery from qualification when the candidate universe is unknown.
5. Store non-trivial research in a durable ledger; conversation context is not
   the source of truth.
6. Use deterministic code for parsing, normalization, schema checks, exact
   deduplication, joins, and counts. Use model judgment for semantic conflicts.
7. Never claim an open-web result is exhaustive. Claim completeness for a
   bounded universe only after its declared source boundaries were traversed.

## 1. Define the research contract

Resolve choices that change membership in the set; ask one concise clarification
only when a safe assumption would materially change the result. Record:

```text
Goal and research object:
Universe: known list | bounded source | open web
As-of date or time range:
Include when / exclude when:
Required fields and field semantics:
Evidence threshold:
Output format:
Stopping rule and budget:
```

Each row needs a stable identifier, normalized fields, qualification status,
claim-level evidence, gaps, conflicts, and confidence. Missing values remain
`unknown`; they are never inferred during synthesis.

For substantial work, use workflow-stage todos such as contract and pilot,
collection, validation, gap filling, and delivery. Keep partition and item state
in the ledger, not in hundreds of todo entries.

## 2. Map and pilot the sources

Do not dispatch collection workers until the main agent has determined:

- whether the universe is known, bounded by authoritative sources, or open-web
- the preferred source for each part of the universe and allowed fallbacks
- whether one source covers the full time range or where source boundaries change
- how pagination, date limits, categories, and official totals prove coverage
- which populated fields need primary evidence or an independent cross-check
- the shared extraction, normalization, and deduplication method

If several workers would rediscover the same source or method, resolve it once
centrally before fan-out. Different source segments are acceptable only when the
source map records their coverage boundary, overlapping range, normalization
rule, and reconciliation method.

Pilot the complete method on a small, varied sample before scaling. Include an
ordinary row, an exclusion or missing value, and an ambiguous or conflicting
case when available. Confirm that the source is traversable, the schema fits,
evidence supports individual fields, and expected cost is acceptable. Revise the
contract or source strategy before launching a large batch.

## 3. Create the durable ledger

A durable ledger is required when the work has multiple partitions or waves,
may be compacted or truncated, or needs validation, retries, deduplication, or
conflict tracking.

Choose the internal format for the state shape:

- **CSV** for flat rows with a fixed schema
- **JSONL** for aliases, multiple evidence records, conflicts, and attempt history
- **SQLite** for large datasets or frequent querying and updates
- **Excel** as a user-facing export, not the internal source of truth

Track at least:

```text
partition_id and owned boundary
canonical item ID
processing status and attempt count
qualification decision and reason
normalized fields
claim-level evidence and provenance
gaps, conflicts, and exclusion reason
last completed research phase
```

Use explicit states such as `pending`, `processing`, `verified`, `excluded`,
`uncertain`, and `failed`. Absence of a row never means completion.

Subagents must not concurrently edit the same ledger file. Each worker returns
structured records or writes a partition-specific file. The main agent validates
and merges results into the canonical ledger after each wave. Generate the final
dataset and optional Excel workbook deterministically from that ledger; do not
use the user-facing export as mutable research state.

## 4. Partition into independent waves

Choose boundaries from the universe and source strategy:

- **Known list:** assign each input once, individually or in balanced batches.
- **Bounded source:** split non-overlapping page ranges, registry segments,
  categories, or date intervals from the already selected source.
- **Open web:** use orthogonal discovery lanes such as geography, terminology,
  source type, or category; later validate the merged candidate set separately.

Time is a valid partition only after the shared source, filters, schema, and
validation method are fixed. Do not mechanically assign different years to
workers who must each rediscover the same data source and invent a method.

Before a wave, record a partition manifest containing the partition ID, exact
owned boundary, explicit non-overlap, input artifact, expected output path or
schema, and terminal status. Tasks in one wave must be independently completable
from inputs that already exist. Do not dispatch qualification, enrichment, or
synthesis tasks before their candidate or collection artifacts exist.

Every subagent brief must carry the shared contract plus its exact slice, source
policy, validation checks, output schema, and boundaries. Workers must return
excluded, uncertain, failed, and unprocessed items explicitly and must not write
the final report.

## 5. Merge, validate, and fill gaps

After each wave, the main agent must:

1. validate every partition output against the shared schema
2. merge it into the canonical ledger without losing provenance
3. normalize exact identifiers, dates, units, names, and URLs deterministically
4. inspect semantic duplicates, aliases, renamed entities, and source conflicts
5. reconcile row counts against known inputs, traversed boundaries, or official totals
6. mark each partition and item with an explicit state
7. launch another wave only for a named gap, conflict, or failed partition

Discovery evidence makes an item a candidate, not automatically `verified`.
Qualification should prefer primary sources and check every inclusion rule and
populated field. Keep weak or conflicting records `uncertain` unless a targeted
verifier resolves them.

Measure coverage by universe type:

- known list: every input has a terminal state and field-completeness metrics
- bounded source: every declared boundary was traversed and counts reconciled
- open web: report lanes attempted, overlap, new candidates by wave, and weak areas

Stop when all known inputs or bounded partitions have terminal states, or when
the declared open-web lanes and gap-filling budget are exhausted. Budget
exhaustion is a limitation, not evidence of completeness.

## 6. Deliver honestly

Deliver the normalized dataset before the narrative. The summary should state
the contract and as-of date, headline counts, artifact location, status counts,
unresolved gaps and conflicts, coverage method, stopping condition, and limits
on completeness. Preserve source URLs or citation markers with the claims they
support.

Before finalizing, verify that every partition is accounted for, accepted fields
have evidence, duplicates retain provenance, uncertain and failed records remain
visible, and the completeness language matches the measured coverage. If not,
run a targeted correction wave or disclose the limitation.
