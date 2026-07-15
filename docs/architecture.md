# Architecture

## Phase A vertical slice

```text
Next.js workbench
       │ HTTP + SSE
FastAPI domain API ── command bus ── SQLAlchemy domain store + append-only events
       │
LangGraph research graph ── independent SQLite checkpointer
       │
PI ── Librarian/OpenAlex ── Skeptic ── human meeting interrupt ── Writer
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

## Harness boundary

`oplab.harness` exposes model, tool, checkpoint, budget, and policy ports. The included local
implementation is sufficient for phase A. A DeerFlow adapter may implement those ports without
allowing DeerFlow thread/session types to enter `oplab.domain`. Upstream DeerFlow remains an
independent MIT-licensed project; Multica code is not used.

## Deferred by design

- Temporal schedules, signals, and cross-day project workflows (phase B)
- Durable long-term role memory and unattended meetings (phase B)
- Isolated experiment executor and reproduction runs (phase C)
- A2A federation and multi-tenant institutional policy (phase D)

These boundaries are explicit so a placeholder cannot be mistaken for a security or durability
guarantee.
