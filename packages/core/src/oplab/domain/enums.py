from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    REFUTED = "refuted"


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    OPPOSES = "opposes"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_USER = "needs_user"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class MeetingStatus(StrEnum):
    PREPARING = "preparing"
    WAITING_USER = "waiting_user"
    DECIDED = "decided"
    CANCELLED = "cancelled"


class MeetingDecisionKind(StrEnum):
    CONTINUE = "continue"
    REVISE = "revise"
    STOP = "stop"


class StopReason(StrEnum):
    SUCCESS = "success"
    NEEDS_USER = "needs_user"
    BLOCKED_EXTERNAL = "blocked_external"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    POLICY_DENIED = "policy_denied"
    CONTRADICTION_UNRESOLVED = "contradiction_unresolved"
    FAILED = "failed"


class ResearchPhase(StrEnum):
    CHARTER = "charter"
    LIBRARIAN = "librarian"
    SKEPTIC = "skeptic"
    MEETING = "meeting"
    WRITER = "writer"
    COMPLETE = "complete"
