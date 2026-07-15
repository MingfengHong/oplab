# Architecture

## Dynamic research harness

```text
Next.js workbench
       │ same-origin HTTP proxy + SSE
FastAPI domain API ── command bus ── SQLAlchemy domain store + append-only events
       │
LangGraph durable shell ── independent SQLite checkpointer
       │
plan → controller → typed tool → evaluator ─┐
          ▲                                │
          └──────── evidence gaps ─────────┘
          │
human meeting interrupt → draft → independent reviewer → publish
       │
upload/artifact store
```

PostgreSQL is the production authority for research objects. SQLite is a documented local
development substitute, not a second production authority. LangGraph checkpoint tables are
kept out of the domain schema. Each run records `project_id`, `run_id`, `thread_id`, and
`trace_id` so later Temporal workflows can add `workflow_id` without changing domain identity.

## Commands and events

Agents return proposals. Only the domain service validates and applies commands such as
`AddEvidence`, `ProposeClaim`, `ContestClaim`, `RequestMeeting`, and `PublishArtifact`.
Successful writes append corresponding events to the same transaction. API consumers receive
events only after commit.

Every command with a side effect carries an idempotency key derived from project, run, action,
input hash, and generation. Replaying a completed node therefore does not duplicate sources,
claims, meetings, or artifacts.

## Recovery model

The graph checkpoints after every node. A persisted run is either `running`, `needs_user`,
`completed`, `cancelled`, or `failed`. At API startup, the recovery service re-enqueues runs
left in `running`; a `needs_user` run remains paused until a meeting decision resumes the same
LangGraph thread. Artifacts are written atomically and registered only after the final path is
available.

## Harness runtime

`oplab.harness` is a real decision runtime rather than a role pipeline:

- `schemas.py` defines plans, actions, tool outcomes, evidence evaluations, claim extraction,
  challenge links, and review verdicts.
- `controller.py` asks the configured model for exactly one typed decision, then validates tool
  arguments and policy gates. Malformed structured output is repaired through schema-guided retry.
- `tools.py` is the permissioned registry. Search is read-only; all evidence, claim, challenge,
  meeting, and artifact writes still pass through domain commands.
- `policy.py` supplies deterministic evaluation, offline fallback decisions, iteration/search/source
  budgets, and a repeated-action fingerprint guard.
- LangGraph provides recovery and interrupts only. The research strategy lives in persisted plan,
  evidence state, controller decisions, and evaluator feedback—not graph node names.

The design was informed by Synthetic Sciences OpenScience at commit
`e9844a49f1f4d93cbf5f88b8f4880c003adc6e61` (Apache-2.0): especially its dynamic tool loop,
persisted research state, reviewer/subagent gates, trajectory capture, budgets, and anti-loop
guards. Oplab is an independent Python implementation under AGPL-3.0-or-later.

## Deferred by design

- Temporal schedules, signals, and cross-day project workflows (phase B)
- Durable long-term learned-skill distillation and unattended meetings (phase B)
- Isolated experiment executor and reproduction runs (phase C)
- A2A federation and multi-tenant institutional policy (phase D)

These boundaries are explicit so a placeholder cannot be mistaken for a security or durability
guarantee.
