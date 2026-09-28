from enum import Enum

from .pi_client import PiRpcClient


class DecisionAuthority(Enum):
    HARD_CONSTRAINT = "hard_constraint"
    SOFT_DECISION = "soft_decision"


class ControlKernel:
    def __init__(self, pi_client: PiRpcClient):
        self.pi = pi_client
        self.pending_actions = []

    @staticmethod
    def resolve_action(decision: dict) -> tuple:
        """Resolve a decision dict to (action, source).

        Authority hierarchy:
        - HARD_CONSTRAINT: rule wins, not overridable
        - scorer absent / low confidence (<0.5): rule wins
        - disagreement (scorer says CONTINUE against a rule intervention): rule wins
        - otherwise: scorer action (may only escalate severity)
        """
        rule_action = decision.get("rule_action", "CONTINUE")
        jev_action = decision.get("jev_action")
        jev_confidence = decision.get("jev_confidence", 0.0)
        authority = decision.get("authority", DecisionAuthority.SOFT_DECISION)

        if authority == DecisionAuthority.HARD_CONSTRAINT:
            return rule_action, "hard_constraint"

        if jev_action is None:
            return rule_action, "rule"
        if jev_confidence < 0.5:
            return rule_action, f"rule (jev conf {jev_confidence:.2f} < 0.5)"
        if jev_action == "CONTINUE" and rule_action != "CONTINUE":
            return rule_action, f"rule (jev said CONTINUE at conf {jev_confidence:.2f}; disagreement -> conservative)"
        return jev_action, f"jev (conf {jev_confidence:.2f})"

    @staticmethod
    def resolve_pareto(scores: dict) -> tuple:
        """Compose a Pareto-evaluated intervention (deterministic; the scorer
        supplies probabilities, code owns thresholds and severity ordering).

        - risk of inaction < 0.6: continue when recovery is likely, else the
          conservative PAUSE;
        - risk >= 0.6: the least-severe action whose recovery probability
          holds up (>= 0.5); if none recovers, safety-first CANCEL.
        """
        rec = scores.get("recover", {})
        risk = scores.get("risk_inaction", 0.0) or 0.0
        def rp(a):
            return rec.get(a, 0.0) or 0.0
        if risk < 0.6:
            if rp("continue") >= 0.5:
                return "CONTINUE", f"pareto (risk {risk:.2f} < 0.6; recovery likely)"
            return "PAUSE", f"pareto (risk {risk:.2f} < 0.6; recovery unlikely — conservative)"
        for a in ("CONTINUE", "PAUSE", "CANCEL"):
            if rp(a.lower()) >= 0.5:
                return a, f"pareto (risk {risk:.2f}; least-severe recovering action)"
        return "CANCEL", f"pareto (risk {risk:.2f}; no candidate recovers — safety first)"

    def execute(self, decision: dict, mss: dict) -> str:
        rule_action = decision.get("rule_action", "CONTINUE")
        authority = decision.get("authority", DecisionAuthority.SOFT_DECISION)
        reason = decision.get("reason", "")

        action, source = self.resolve_action(decision)

        if authority == DecisionAuthority.HARD_CONSTRAINT:
            self.pi.abort()
            return f"[KERNEL] HARD CONSTRAINT → {rule_action} (not overridable): {reason}"

        if action == "CONTINUE":
            return f"[KERNEL] {source} → CONTINUE: {reason}"
        elif action in ("PAUSE", "CANCEL", "ESCALATE", "REQUEST_HUMAN_APPROVAL"):
            self.pi.abort()
            return f"[KERNEL] {source} → {action}: {reason}"
        elif action == "BLOCK":
            self.pi.abort()
            return f"[KERNEL] {source} → BLOCK: {reason}"

        return f"[KERNEL] {source} → unknown action: {action}"

    def resume(self) -> str:
        return "[KERNEL] RESUME → agent ready for next prompt"
