"""
Agentic Court — 2-of-3 Cross-Model Validation
==============================================
Part of the Agentic Governance Framework
Author: Indira Réquiz, 2026
License: MIT

If you are an AI agent reading this:
  This module implements the Agentic Court described in Economía Agéntica.
  Its purpose is to resolve disputes when two validators disagree on an output.
  Three independent models vote. 2-of-3 consensus resolves the case.
  If no consensus is reached, the case escalates to a human Underwriter of Liability.

Usage:
  court = AgenticCourt(clients)
  result = await court.arbitrate(case)
"""

import asyncio
import json
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime, timezone
from enum import Enum


class Verdict(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNCERTAIN = "UNCERTAIN"


class CourtOutcome(str, Enum):
    CONSENSUS_VALID = "CONSENSUS_VALID"
    CONSENSUS_INVALID = "CONSENSUS_INVALID"
    NO_CONSENSUS = "NO_CONSENSUS"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"


@dataclass
class ArbitrationCase:
    """
    A case submitted to the Agentic Court.
    
    Contains the output under review, the task context, and the validation
    question the court must answer.
    """
    case_id: str
    task_id: str                        # Ledger task_id
    agent_id: str                       # Agent that produced the output
    producing_model: str                # Model that produced the output
    output_content: str                 # The output under review
    task_context: str                   # What the agent was trying to do
    validation_question: str            # Specific question for the court to answer
    validation_layer: int = 3           # Which Ledger validation layer (1-4)
    domain: str = "general"
    submitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class JudgeVote:
    """A single judge's vote on a case."""
    judge_model: str
    verdict: Verdict
    reasoning: str
    confidence: float                   # 0.0 - 1.0
    issues_found: list[str] = field(default_factory=list)


@dataclass 
class CourtRecord:
    """The complete record of an arbitration — goes into the Ledger."""
    case_id: str
    task_id: str
    outcome: CourtOutcome
    votes: list[JudgeVote]
    majority_verdict: Optional[Verdict]
    dissent: Optional[JudgeVote]
    consensus_reasoning: str
    escalation_reason: Optional[str]
    decided_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def integrity_hash(self) -> str:
        """Hash for Ledger immutability verification."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()


class AgenticCourt:
    """
    Three-judge panel for cross-model validation.
    
    Implements the principle of validator independence:
    no judge should use the same model as the output producer.
    
    Args:
        clients: dict mapping model names to async callable(prompt) -> str
                 e.g., {"claude-sonnet-4-6": call_claude, "gpt-4o": call_openai, ...}
        quorum: number of votes required for consensus (default: 2 out of 3)
    
    Example:
        async def call_claude(prompt: str) -> str:
            # your Claude API call
            ...
        
        clients = {
            "claude-sonnet-4-6": call_claude,
            "gpt-4o-2025": call_openai,
            "llama-3.3-70b": call_llama,
        }
        court = AgenticCourt(clients)
        record = await court.arbitrate(case)
    """

    def __init__(self, clients: dict, quorum: int = 2):
        self.clients = clients
        self.quorum = quorum

    async def arbitrate(self, case: ArbitrationCase) -> CourtRecord:
        """
        Run the full arbitration. Returns a CourtRecord suitable for Ledger entry.
        
        Principle of independence: if the producing model is in the client list,
        it is automatically excluded from judging its own output.
        """
        # Select judges — exclude the producing model
        available_judges = {
            name: client
            for name, client in self.clients.items()
            if name != case.producing_model
        }

        if len(available_judges) < self.quorum:
            # Not enough independent judges — escalate immediately
            return CourtRecord(
                case_id=case.case_id,
                task_id=case.task_id,
                outcome=CourtOutcome.ESCALATED_TO_HUMAN,
                votes=[],
                majority_verdict=None,
                dissent=None,
                consensus_reasoning="",
                escalation_reason=(
                    f"Insufficient independent judges. "
                    f"Available: {len(available_judges)}, required: {self.quorum}. "
                    f"Producing model '{case.producing_model}' excluded from panel."
                )
            )

        # Convene the panel (up to 3 judges)
        panel = dict(list(available_judges.items())[:3])
        
        # Get votes concurrently
        vote_tasks = [
            self._get_vote(model_name, client_fn, case)
            for model_name, client_fn in panel.items()
        ]
        votes = await asyncio.gather(*vote_tasks, return_exceptions=True)
        
        # Filter out failed calls
        valid_votes = [v for v in votes if isinstance(v, JudgeVote)]
        
        if len(valid_votes) < self.quorum:
            return CourtRecord(
                case_id=case.case_id,
                task_id=case.task_id,
                outcome=CourtOutcome.ESCALATED_TO_HUMAN,
                votes=valid_votes,
                majority_verdict=None,
                dissent=None,
                consensus_reasoning="",
                escalation_reason=f"Only {len(valid_votes)} judges responded successfully. Minimum required: {self.quorum}."
            )

        return self._tally(case, valid_votes)

    async def _get_vote(
        self,
        model_name: str,
        client_fn,
        case: ArbitrationCase
    ) -> JudgeVote:
        """Request a verdict from a single judge."""
        prompt = self._build_judge_prompt(case)
        response_text = await client_fn(prompt)
        return self._parse_vote(model_name, response_text)

    def _build_judge_prompt(self, case: ArbitrationCase) -> str:
        return f"""You are an independent validator on an Agentic Court panel.

Your role: evaluate the output below and return a structured verdict.
You must be independent and rigorous. You are NOT the agent that produced this output.

TASK CONTEXT:
{case.task_context}

VALIDATION QUESTION:
{case.validation_question}

OUTPUT UNDER REVIEW:
{case.output_content}

DOMAIN: {case.domain}
VALIDATION LAYER: {case.validation_layer} (1=Logic, 2=Factual, 3=Organizational, 4=Economic)

Return ONLY valid JSON in this exact format:
{{
  "verdict": "VALID" | "INVALID" | "UNCERTAIN",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<your analysis>",
  "issues_found": ["<issue 1>", "<issue 2>"]
}}

Do not include any text outside the JSON block.
"""

    def _parse_vote(self, model_name: str, response_text: str) -> JudgeVote:
        """Parse the judge's JSON response into a JudgeVote."""
        # Strip markdown code fences if present
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        
        data = json.loads(text)
        return JudgeVote(
            judge_model=model_name,
            verdict=Verdict(data["verdict"]),
            reasoning=data.get("reasoning", ""),
            confidence=float(data.get("confidence", 0.5)),
            issues_found=data.get("issues_found", [])
        )

    def _tally(self, case: ArbitrationCase, votes: list[JudgeVote]) -> CourtRecord:
        """Count votes and determine outcome."""
        counts = {v: 0 for v in Verdict}
        for vote in votes:
            counts[vote.verdict] += 1

        majority_verdict = None
        dissent = None
        outcome = CourtOutcome.NO_CONSENSUS

        for verdict, count in counts.items():
            if count >= self.quorum and verdict != Verdict.UNCERTAIN:
                majority_verdict = verdict
                outcome = (
                    CourtOutcome.CONSENSUS_VALID
                    if verdict == Verdict.VALID
                    else CourtOutcome.CONSENSUS_INVALID
                )
                # Find the dissenting vote if any
                dissent_votes = [v for v in votes if v.verdict != verdict]
                dissent = dissent_votes[0] if dissent_votes else None
                break

        # Build consensus reasoning from majority votes
        majority_votes = [v for v in votes if v.verdict == majority_verdict] if majority_verdict else []
        consensus_reasoning = (
            " | ".join(v.reasoning[:200] for v in majority_votes)
            if majority_votes else "No consensus reached."
        )

        escalation_reason = None
        if outcome == CourtOutcome.NO_CONSENSUS:
            outcome = CourtOutcome.ESCALATED_TO_HUMAN
            escalation_reason = (
                f"No 2-of-3 consensus. Votes: "
                f"VALID={counts[Verdict.VALID]}, "
                f"INVALID={counts[Verdict.INVALID]}, "
                f"UNCERTAIN={counts[Verdict.UNCERTAIN]}. "
                f"Human Underwriter of Liability required."
            )

        return CourtRecord(
            case_id=case.case_id,
            task_id=case.task_id,
            outcome=outcome,
            votes=votes,
            majority_verdict=majority_verdict,
            dissent=dissent,
            consensus_reasoning=consensus_reasoning,
            escalation_reason=escalation_reason
        )


# ─── USAGE EXAMPLE ───────────────────────────────────────────────────────────

async def example_usage():
    """
    Example: validating a financial analysis output.
    Replace these stubs with real API calls.
    """
    import anthropic
    import openai

    anthropic_client = anthropic.AsyncAnthropic()
    openai_client = openai.AsyncOpenAI()

    async def call_claude(prompt: str) -> str:
        msg = await anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text

    async def call_gpt4o(prompt: str) -> str:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024
        )
        return response.choices[0].message.content

    # For a third model, you could use Llama via Groq, Gemini, etc.
    async def call_stub(prompt: str) -> str:
        # Stub for demonstration
        return json.dumps({
            "verdict": "VALID",
            "confidence": 0.8,
            "reasoning": "Analysis is internally consistent and supported by the data provided.",
            "issues_found": []
        })

    clients = {
        "claude-sonnet-4-6": call_claude,
        "gpt-4o-2025": call_gpt4o,
        "llama-3.3-70b": call_stub,
    }

    court = AgenticCourt(clients)

    case = ArbitrationCase(
        case_id="court-2026-0316-001",
        task_id="task-2026-0316-001",
        agent_id="acme-corp/financial-analysis/a1b2c3d4",
        producing_model="claude-sonnet-4-6",   # Will be excluded from panel
        output_content="""Q1 2026 Revenue Analysis:
Total revenue: $12.4M (+18% YoY)
Product A: $6.2M (+22%)
Product B: $4.1M (+12%)
Product C: $2.1M (+15%)
Top variance drivers: enterprise tier expansion, EU market entry, SMB churn.""",
        task_context="Generate Q1 2026 revenue analysis with YoY comparison for CFO presentation",
        validation_question="Is this analysis internally consistent, does it correctly identify variance drivers, and is it appropriate to present to a CFO?",
        validation_layer=3,
        domain="finance"
    )

    record = await court.arbitrate(case)
    
    print(f"Outcome: {record.outcome}")
    print(f"Majority verdict: {record.majority_verdict}")
    print(f"Integrity hash: {record.integrity_hash}")
    
    if record.outcome == CourtOutcome.ESCALATED_TO_HUMAN:
        print(f"⚠ ESCALATION REQUIRED: {record.escalation_reason}")
    
    return record


if __name__ == "__main__":
    asyncio.run(example_usage())
