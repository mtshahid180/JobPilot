"""Tool permission tiers and the human approval gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from jobpilot.core.context import LOCAL_USER_ID, current_user_id
from jobpilot.core.db import Database, from_json, get_db, to_json
from jobpilot.core.models import Approval, utcnow

READ = "read"
WRITE_LOCAL = "write_local"
EXTERNAL = "external"
CONFIRMATION = "confirmation"

TIER_LABELS = {
    READ: "Runs automatically",
    WRITE_LOCAL: "Runs automatically, logged",
    CONFIRMATION: "Needs your confirmation",
    EXTERNAL: "Needs explicit approval",
}


@dataclass
class PermissionDecision:
    allowed: bool
    requires_approval: bool
    tier: str
    reason: str = ""


class PermissionPolicy:
    def __init__(self, tiers: dict[str, str] | None = None) -> None:
        self.tiers: dict[str, str] = dict(tiers or {})

    def register(self, tool: str, tier: str) -> None:
        self.tiers[tool] = tier

    def tier_for(self, tool: str) -> str:
        return self.tiers.get(tool, EXTERNAL)

    def decide(self, tool: str, args: dict | None = None) -> PermissionDecision:
        tier = self.tier_for(tool)
        if tier == READ:
            return PermissionDecision(True, False, tier, "read-only")
        if tier == WRITE_LOCAL:
            return PermissionDecision(True, False, tier, "writes locally and is logged")
        if tier == CONFIRMATION:
            return PermissionDecision(False, True, tier, "profile-affecting change")
        if tier == EXTERNAL:
            return PermissionDecision(False, True, tier, "external action requires approval")
        return PermissionDecision(False, True, EXTERNAL, "unknown tier defaults to approval")


class ApprovalService:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def create(
        self,
        tool: str,
        args: dict,
        reason: str = "",
        run_id: str = "",
        tier: str = EXTERNAL,
    ) -> Approval:
        approval = Approval(
            run_id=run_id,
            tool=tool,
            args=args,
            reason=reason,
            tier=tier,
        )
        self.db.execute(
            "INSERT INTO approvals (id, user_id, run_id, tool, args, reason, tier, status, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                approval.id,
                current_user_id(),
                approval.run_id,
                approval.tool,
                to_json(approval.args),
                approval.reason,
                approval.tier,
                approval.created_at.isoformat(),
            ),
        )
        return approval

    def get(self, approval_id: str) -> Approval | None:
        row = self.db.query_one(
            "SELECT * FROM approvals WHERE id = ? AND user_id = ?",
            (approval_id, current_user_id()),
        )
        return self._to_model(row) if row else None

    def list(self, status: str = "pending", limit: int = 100) -> list[Approval]:
        if status == "all":
            rows = self.db.query(
                "SELECT * FROM approvals WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (current_user_id(), limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM approvals WHERE status = ? AND user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, current_user_id(), limit),
            )
        return [self._to_model(row) for row in rows]

    def pending_count(self) -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM approvals WHERE status = 'pending' AND user_id = ?",
                (current_user_id(),),
                default=0,
            )
        )

    def decide(self, approval_id: str, approve: bool) -> Approval | None:
        approval = self.get(approval_id)
        if not approval or approval.status != "pending":
            return approval
        status = "approved" if approve else "rejected"
        self.db.execute(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ? AND user_id = ?",
            (status, utcnow().isoformat(), approval_id, current_user_id()),
        )
        return self.get(approval_id)

    def _to_model(self, row: dict) -> Approval:
        return Approval(
            id=row["id"],
            run_id=row["run_id"],
            tool=row["tool"],
            args=from_json(row["args"], {}),
            reason=row["reason"],
            tier=row["tier"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        )
