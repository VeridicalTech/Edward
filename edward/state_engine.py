import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .canonical_events import Capability, normalize_event


@dataclass
class ToolCall:
    tool_name: str
    args: dict
    is_error: bool
    duration_ms: float
    started_at: float
    ended_at: float


@dataclass
class AgentState:
    task_goal: str = ""
    agent_status: str = "idle"
    turn_count: int = 0
    total_tool_calls: int = 0
    total_errors: int = 0
    recent_tool_calls: list[ToolCall] = field(default_factory=list)
    retry_count: int = 0
    token_usage: int = 0
    token_budget: int = 200_000
    cost_usd: float = 0.0
    started_at: float = 0.0
    last_activity: float = 0.0
    files_modified: set[str] = field(default_factory=set)
    dangerous_commands: list[str] = field(default_factory=list)
    scope_violations: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    consecutive_unverified_writes: int = 0
    total_writes: int = 0
    clock: Optional[callable] = None  # injectable for replay determinism

    @property
    def error_rate(self) -> float:
        recent = self.recent_tool_calls[-10:]
        if not recent:
            return 0.0
        return sum(1 for tc in recent if tc.is_error) / len(recent)

    @property
    def budget_pct(self) -> float:
        if self.token_budget == 0:
            return 0.0
        return self.token_usage / self.token_budget

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at == 0:
            return 0.0
        return (self.clock or time.time)() - self.started_at

    @property
    def recovery_signal(self) -> bool:
        """True if the agent recovered after recent errors.

        Recovery = within the sliding window there are errors followed by
        a run of >= 3 consecutive successes at the end.
        """
        recent = self.recent_tool_calls[-10:]
        if len(recent) < 5:
            return False
        has_errors = any(tc.is_error for tc in recent)
        tail = list(reversed(recent))
        success_streak = 0
        for tc in tail:
            if tc.is_error:
                break
            success_streak += 1
        return has_errors and success_streak >= 3

    @property
    def error_trend_rising(self) -> bool:
        """True if the second half of the window has a higher error rate than the first."""
        recent = self.recent_tool_calls[-10:]
        if len(recent) < 6:
            return False
        mid = len(recent) // 2
        first = recent[:mid]
        second = recent[mid:]
        rate_first = sum(1 for tc in first if tc.is_error) / len(first)
        rate_second = sum(1 for tc in second if tc.is_error) / len(second)
        return rate_second > rate_first

    def in_scope(self, path: str) -> bool:
        if not self.allowed_paths:
            return True
        return any(path.startswith(prefix) for prefix in self.allowed_paths)

    def to_mss(self) -> dict:
        return {
            "task_goal": self.task_goal,
            "agent_status": self.agent_status,
            "turn_count": self.turn_count,
            "total_tool_calls": self.total_tool_calls,
            "total_errors": self.total_errors,
            "recent_error_rate": round(self.error_rate, 3),
            "retry_count": self.retry_count,
            "token_usage": self.token_usage,
            "token_budget_pct": round(self.budget_pct, 3),
            "cost_usd": round(self.cost_usd, 4),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "files_modified_count": len(self.files_modified),
            "dangerous_commands": self.dangerous_commands[-5:],
            "scope_violations": self.scope_violations[-5:],
            "consecutive_unverified_writes": self.consecutive_unverified_writes,
            "recovery_signal": self.recovery_signal,
            "error_trend_rising": self.error_trend_rising,
        }


DANGEROUS_PATTERNS = [
    re.compile(r"rm\s+-rf"),
    # sudo is a signal, but routine service operations are not escalation
    re.compile(r"\bsudo\b(?!\s+(?:systemctl|service|journalctl)\b)"),
    re.compile(r"drop\s+table", re.IGNORECASE),
    re.compile(r"truncate\s+table", re.IGNORECASE),
    re.compile(r"git\s+push\s+.*--force"),
    re.compile(r"curl\s+.*\|\s*sh"),
    re.compile(r">\s*/dev/sd"),
]


class StateEngine:
    def __init__(self, token_budget: int = 200_000, clock=None):
        self.state = AgentState(token_budget=token_budget, clock=clock)
        self._pending_args: dict[str, dict] = {}

    def _now(self) -> float:
        return (self.state.clock or time.time)()

    def process_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        now = self._now()
        self.state.last_activity = now

        if event_type == "agent_start":
            self.state.agent_status = "running"
            if self.state.started_at == 0:
                self.state.started_at = now

        elif event_type == "agent_end":
            self.state.agent_status = "idle"

        elif event_type == "agent_settled":
            self.state.agent_status = "settled"

        elif event_type == "turn_start":
            self.state.turn_count += 1

        elif event_type == "message_update":
            usage = event.get("usage", {})
            self.state.token_usage = usage.get("totalTokens", self.state.token_usage)
            cost = usage.get("cost", {})
            self.state.cost_usd = cost.get("total", self.state.cost_usd)

        elif event_type == "tool_execution_start":
            self.state.total_tool_calls += 1
            call_id = event.get("toolCallId", "")
            if call_id:
                self._pending_args[call_id] = event.get("args", {})

        elif event_type == "tool_execution_end":
            self._handle_tool_end(event, now)

        elif event_type == "auto_retry_start":
            self.state.retry_count += 1

    def _handle_tool_end(self, event: dict, now: float) -> None:
        tool_name = event.get("toolName", "") or event.get("tool_name", "")
        is_error = event.get("isError", False)
        args = event.get("args") or self._pending_args.pop(event.get("toolCallId", ""), {})

        from .canonical_events import resolve_capability
        capability = resolve_capability(tool_name)

        if capability == Capability.SHELL_EXECUTION:
            command = args.get("command", "")
            if not command:
                command = str(args)
            for pattern in DANGEROUS_PATTERNS:
                if pattern.search(command):
                    self.state.dangerous_commands.append(command)

        if capability in (Capability.FILE_WRITE, Capability.FILE_EDIT):
            file_path = args.get("path", "") or args.get("file_path", "")
            if file_path:
                self.state.files_modified.add(file_path)
                self.state.total_writes += 1
                if not self.state.in_scope(file_path):
                    self.state.scope_violations.append(file_path)

        if capability == Capability.SHELL_EXECUTION:
            self.state.consecutive_unverified_writes = 0
        elif capability in (Capability.FILE_WRITE, Capability.FILE_EDIT):
            self.state.consecutive_unverified_writes += 1

        tc = ToolCall(
            tool_name=tool_name,
            args=args,
            is_error=is_error,
            duration_ms=0,
            started_at=now,
            ended_at=now,
        )
        self.state.recent_tool_calls.append(tc)
        if is_error:
            self.state.total_errors += 1

        if len(self.state.recent_tool_calls) > 50:
            self.state.recent_tool_calls = self.state.recent_tool_calls[-50:]
