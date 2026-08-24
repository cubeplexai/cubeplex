---
name: wide-research
description: >
  Builds comprehensive, evidence-backed lists or datasets by partitioning many
  independent research items across parallel subagents, then validating,
  deduplicating, and gap-filling the combined results. Use for requests to find
  all or many matching entities, map a market or literature set, enrich a known
  list, or apply the same research contract across many inputs; use
  deep-research instead for one topic that needs multi-step investigation.
version: 1.0.0
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

Build a verifiable set of research rows, not a pile of mini-reports. The main
agent owns the research contract, partition plan, shared ledger, validation,
coverage assessment, and final deliverable. Subagents independently discover or
verify bounded slices and return structured evidence.

## Choose the right research mode

Use this workflow when breadth is the hard part:

- enumerate all or many entities satisfying shared constraints
- research the same fields for a known list of inputs
- build a market map, catalog, landscape, bibliography, or comparison matrix
- search multiple independent regions, categories, registries, or source types
- produce a structured dataset whose missing and uncertain rows must remain visible

Use `deep-research` instead when the task centers on one subject, depends on a
chain of discoveries, or mainly needs causal analysis and narrative synthesis.
Use direct search for one quick fact. For mixed tasks, run wide discovery first,
then investigate only ambiguous or high-value rows in depth.

## Non-negotiable invariants

1. Define the set and row schema before large-scale dispatch.
2. Partition by independent objects or orthogonal discovery lanes. Do not give
   multiple subagents the same vague topic.
3. Every accepted field must trace to evidence. Missing means `unknown`, never a guess.
4. Preserve excluded, uncertain, failed, and unprocessed items; never silently drop them.
5. Separate discovery from qualification when the candidate universe is unknown.
6. Use deterministic code for mechanical normalization, schema validation, set
   operations, and exact deduplication when tools permit. Reserve model judgment
   for semantic aliases, conflicting evidence, and qualification decisions.
7. Never claim an open-web result is exhaustive unless the universe is bounded by
   an authoritative source that was fully traversed.

## Workflow

### 1. Ground the request

Before creating a large plan or dispatching subagents, determine:

- **research object** — company, product, paper, policy, repository, person, etc.
- **universe** — known input list, bounded registry/catalog, or unknown open web
- **inclusion and exclusion rules** — conditions a row must satisfy
- **required fields** — the shared output columns
- **time boundary** — as-of date, publication window, or event period
- **evidence threshold** — preferred source types and any cross-check requirement
- **deliverable** — table, JSON/CSV, report, files, or a combination
- **budget** — useful limits on waves, tool calls, cost, or wall time

If the request is time-sensitive, call the available datetime tool first and
carry that date into every subagent brief. If a missing choice would materially
change membership in the set, ask one concise clarification. Otherwise state a
reasonable assumption and proceed.

### 2. Write the research contract

Record a compact contract before fan-out. At minimum it must contain:

```text
Goal:
Universe:
As-of date / time range:
Include when:
Exclude when:
Required fields:
Evidence standard:
Output format:
Stopping rule:
```

Define field semantics precisely enough that independent workers make compatible
decisions. For example, distinguish native support from integrations, current
CEO from founder, announced availability from generally available, and calendar
year from fiscal year.

Use a row contract shaped like this unless the task needs another schema:

```json
{
  "canonical_name": "...",
  "aliases": [],
  "status": "verified|excluded|uncertain|failed",
  "qualification": {
    "decision": "include|exclude|unknown",
    "reason": "..."
  },
  "fields": {},
  "evidence": [
    {
      "claim": "...",
      "source_title": "...",
      "source_url": "https://...",
      "source_date": "YYYY-MM-DD|unknown",
      "evidence_summary": "..."
    }
  ],
  "gaps": [],
  "conflicts": [],
  "confidence": "high|medium|low"
}
```

Keep claim-level evidence: one homepage URL attached to an entire row is not
sufficient when different fields come from different sources.

### 3. Create workflow-stage todos

For a substantial run, use `write_todos` to track phases, not individual
entities or subagents. Keep exactly one phase active. A typical plan is:

```text
1. Define and sample-check the research contract
2. Run broad discovery or known-list collection
3. Validate candidates and resolve conflicts
4. Measure coverage and fill material gaps
5. Produce the dataset and research summary
```

The partition manifest and item statuses belong in the research ledger, not in
hundreds of todo entries.

### 4. Pilot before scaling

Run the full contract against a small, varied sample before wide dispatch. Choose
examples likely to expose ambiguity, not merely the easiest rows. Check whether:

- workers interpret inclusion rules consistently
- every required field can be represented by the schema
- evidence supports individual claims
- `unknown`, exclusion, and source conflict cases are expressible
- one row's typical cost and runtime fit the budget

Revise the contract once if the pilot exposes a structural problem. Do not launch
dozens of workers with a broken schema.

### 5. Partition the work

Choose the partition strategy from the universe type.

#### Known input list

Split by entity or by balanced batches of entities. Each input must have one
owner in the manifest. Use smaller batches for hard or heterogeneous inputs and
larger batches for uniform lookups.

#### Bounded registry or catalog

Partition by non-overlapping page range, category, date interval, or registry
segment. Record the traversal boundaries so coverage can be proven later.

#### Unknown open-web universe

Use multiple orthogonal discovery lanes, such as:

- geography or language
- product/category taxonomy
- source type: official registries, industry directories, papers, repositories
- query family and domain terminology
- time window

Overlap between lanes is useful evidence of saturation, but the lanes must not
all be paraphrases of the same search. Discovery workers return candidates;
separate validation workers decide whether they qualify.

Run independent partitions in parallel up to the available concurrency. If the
input count exceeds practical concurrency, process it through bounded waves.
Do not dispatch dependent validation before its candidates exist.

### 6. Brief subagents with a complete contract

Each `subagent` call must be self-contained because subagents do not see the
conversation. Include:

```text
CONTEXT
- Current date and research goal
- Shared definitions needed for this slice

SLICE
- Exact inputs or discovery boundary owned by this worker
- Explicit non-overlap with other workers

METHOD
- Sources and tools to prefer
- Inclusion/exclusion checks
- Cross-check requirement

OUTPUT
- The shared row schema, one record per candidate/input
- Source URL or preserved citation marker for every factual claim

BOUNDARIES
- Do not write the final report
- Do not expand beyond the assigned slice
- Do not infer missing values
- Return excluded, unknown, and failed rows explicitly
```

For discovery workers, require a stable candidate identifier, aliases, discovery
source, and the reason the candidate may qualify. For validation workers, provide
the candidate record and require a fresh qualification decision rather than a
rubber stamp.

### 7. Maintain a durable ledger

After every wave, merge results into one ledger. For small work, an in-context
table is enough. For larger work, persist JSONL, CSV, or a small database under a
writable workspace path so context compaction cannot erase the set.

Track at least:

- partition ID and assigned boundary
- candidate or input ID
- processing status and attempt count
- normalized row
- claim-level evidence
- unresolved gaps and conflicts
- exclusion reason

Validate every returned record against the schema before merging it. Retry a
malformed or transiently failed slice only within the stated budget; otherwise
keep it as `failed` with the reason.

### 8. Normalize, deduplicate, and validate

Apply deterministic normalization first: whitespace, case, URLs, dates, units,
and exact identifiers. Then review likely semantic duplicates such as aliases,
renamed products, parent/subsidiary relationships, or the same paper in multiple
indexes. Preserve merge provenance so evidence is not lost.

Validation should answer separately:

1. Does the candidate satisfy every required inclusion rule?
2. Does each populated field have evidence that supports that exact value?
3. Are sources current enough for the contract's time boundary?
4. Do sources conflict, and if so can the difference be explained?
5. Is the row `verified`, `excluded`, `uncertain`, or `failed`?

Prefer first-party or primary sources for qualification. Use independent
secondary sources for discovery or cross-checking. If evidence is weak or
conflicting, keep the row uncertain and launch a narrowly scoped verifier only
when resolving it matters to the requested result.

### 9. Measure coverage and run gap-filling waves

Review coverage after each meaningful wave.

For a known list, report:

- total inputs, processed inputs, verified, excluded, uncertain, and failed
- completeness of each required field
- any unprocessed IDs

For a bounded source, also report partitions/pages traversed versus expected.

For an unknown universe, absolute recall is unknowable. Assess method coverage:

- which geographies, categories, languages, source types, and query families ran
- how many new qualified candidates each lane and wave contributed
- how much independent-lane overlap occurred
- which material segments remain weak

Launch another wave only for a named gap, conflict, failed partition, or newly
productive lead. Each wave should be narrower than the last.

Default stopping rules:

- known list: every input has a terminal status
- bounded source: every declared partition was traversed
- open web: required lanes ran and two consecutive gap-filling waves produced no
  material new qualified items, or the agreed budget was reached

Budget exhaustion is a limitation, not evidence of completeness.

### 10. Deliver the dataset before the narrative

The primary deliverable is the normalized dataset. For non-trivial results, save
it as an artifact in the user's requested format; CSV or JSON is preferable for
reuse, with a Markdown summary for humans. Do not squeeze a large result set into
chat and silently truncate it.

The summary should state:

1. research contract and as-of date
2. headline counts and strongest findings
3. link to or location of the complete dataset
4. exclusions, uncertain rows, failures, and unresolved conflicts
5. coverage method and stopping condition
6. limitations on any completeness claim

Preserve inline citations or source URLs in both the dataset and summary. The
reader must be able to trace each accepted claim back to its supporting source.

## Quality gate

Before finalizing, verify:

- every partition has a recorded terminal status
- every input or discovered candidate remains accounted for
- accepted fields have claim-level evidence
- duplicates were merged without losing provenance
- uncertain, excluded, and failed rows are visible
- coverage metrics match the universe type
- the stopping rule was actually met or its failure is disclosed
- “all”, “complete”, and “exhaustive” are used only when justified
- the structured dataset is available without chat truncation

If any item fails, run a targeted correction wave or disclose the limitation.

## Common failure modes

- **Many agents, one vague prompt** — creates duplicate searches rather than breadth.
- **Discovery equals verification** — candidates are presented as qualified results.
- **Free-form mini-reports** — aggregation becomes lossy and inconsistent.
- **One source per row** — individual field claims cannot be audited.
- **Silent tail loss** — timeouts, empty results, and malformed rows disappear.
- **Context-only ledger** — a long run loses state after compaction.
- **Writer fills blanks** — synthesis converts unknowns into hallucinated facts.
- **Search saturation equals completeness** — open-web recall is overstated.
- **Unbounded fan-out** — cost rises without improving independent coverage.
