"""Review pipeline for HelixOS orchestrator.

Implements LLM-based task plan review per PRD Section 9. Reviews are opt-in
and run as background tasks. Uses Claude CLI (``claude -p``) for LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.history_writer import HistoryWriter
from src.models import LLMReview, ReviewState, Task

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Internal models
# ------------------------------------------------------------------


class SynthesisResult(BaseModel):
    """Result of synthesizing multiple reviewer verdicts."""

    score: float
    disagreements: list[str]


# ------------------------------------------------------------------
# JSON schemas for Claude CLI structured output
# ------------------------------------------------------------------

_REVIEW_JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "reject"]},
        "summary": {"type": "string"},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "summary", "suggestions"],
})

_SYNTHESIS_JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "disagreements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "disagreements"],
})


# ------------------------------------------------------------------
# Focus-area system prompts
# ------------------------------------------------------------------

_REVIEW_PROMPTS: dict[str, str] = {
    "feasibility_and_edge_cases": (
        "You are an expert code reviewer focusing on feasibility and edge cases.\n\n"
        "Analyze the following task plan and determine:\n"
        "1. Is this plan technically feasible given the codebase context?\n"
        "2. Are there edge cases or failure modes not addressed?\n"
        "3. Are the acceptance criteria clear and testable?\n\n"
        "Respond in JSON with this exact structure:\n"
        '{"verdict": "approve" or "reject", "summary": "...", "suggestions": ["..."]}'
    ),
    "adversarial_red_team": (
        "You are an adversarial reviewer (red team) looking for risks and "
        "vulnerabilities.\n\n"
        "Analyze the following task plan and determine:\n"
        "1. Could this plan introduce security vulnerabilities?\n"
        "2. Could it break existing functionality?\n"
        "3. Are there architectural risks or hidden dependencies?\n\n"
        "Respond in JSON with this exact structure:\n"
        '{"verdict": "approve" or "reject", "summary": "...", "suggestions": ["..."]}'
    ),
}

_DEFAULT_REVIEW_PROMPT = (
    "You are a code reviewer.\n\n"
    "Analyze the following task plan and determine:\n"
    "1. Is this plan sound?\n"
    "2. Are there issues or improvements needed?\n\n"
    "Respond in JSON with this exact structure:\n"
    '{"verdict": "approve" or "reject", "summary": "...", "suggestions": ["..."]}'
)


# ------------------------------------------------------------------
# Raw response truncation
# ------------------------------------------------------------------

# Maximum size for raw_response before DB write (200KB).
MAX_RAW_RESPONSE_BYTES = 200 * 1024
_TRUNCATION_MARKER = "\n[TRUNCATED at 200KB]"


def _truncate_raw_response(text: str) -> str:
    """Truncate raw_response to MAX_RAW_RESPONSE_BYTES, appending marker if needed."""
    if len(text.encode("utf-8")) <= MAX_RAW_RESPONSE_BYTES:
        return text
    # Truncate by characters, estimating safely
    limit = MAX_RAW_RESPONSE_BYTES - len(_TRUNCATION_MARKER.encode("utf-8"))
    # Encode and truncate at byte boundary
    truncated = text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    return truncated + _TRUNCATION_MARKER


# ------------------------------------------------------------------
# Cost estimation from CLI usage data
# ------------------------------------------------------------------

# Approximate pricing per million tokens (USD).
# Updated for current Claude model pricing.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1m, output_per_1m)
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
}

# Fallback pricing if model not recognized
_DEFAULT_PRICING = (3.0, 15.0)


def _extract_cost_usd(cli_output: dict, model: str) -> float | None:
    """Extract approximate cost in USD from Claude CLI JSON output.

    Looks for a ``usage`` field with ``input_tokens`` and ``output_tokens``.
    Returns None if usage data is unavailable (no crash).

    Args:
        cli_output: Full parsed JSON from Claude CLI stdout.
        model: The model identifier used for pricing lookup.

    Returns:
        Approximate cost in USD, or None if usage data not available.
    """
    usage = cli_output.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    if input_tokens is None or output_tokens is None:
        return None

    try:
        input_tokens = int(input_tokens)
        output_tokens = int(output_tokens)
    except (TypeError, ValueError):
        return None

    input_rate, output_rate = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
    cost = (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
    return round(cost, 6)


# ------------------------------------------------------------------
# ReviewPipeline
# ------------------------------------------------------------------


class ReviewPipeline:
    """LLM-based review pipeline for task plans.

    Uses Claude CLI (``claude -p``) for LLM calls. 1 primary reviewer
    + 1 optional adversarial. Reviews are opt-in and run as background
    tasks (via ``asyncio.create_task``). Results are pushed via SSE.
    """

    def __init__(
        self,
        config: ReviewPipelineConfig,
        threshold: float = 0.8,
        history_writer: HistoryWriter | None = None,
    ) -> None:
        """Initialize the review pipeline.

        Args:
            config: Review pipeline configuration (reviewers list).
            threshold: Consensus score threshold for auto-approval.
            history_writer: Optional DB-first review history writer.
        """
        self.reviewers = [r for r in config.reviewers if r.required]
        self.optional_reviewers = [r for r in config.reviewers if not r.required]
        self.threshold = threshold
        self._history_writer = history_writer

    async def review_task(
        self,
        task: Task,
        plan_content: str,
        on_progress: Callable[[int, int], None],
        complexity: str = "S",
    ) -> ReviewState:
        """Run the review pipeline for a task plan.

        Args:
            task: The task to review.
            plan_content: The plan text to review.
            on_progress: Callback ``(completed, total)`` for progress reporting.
            complexity: Task complexity (``"S"``, ``"M"``, ``"L"``). Adversarial
                reviewer added for M and L tasks.

        Returns:
            ReviewState with all reviews, consensus score, and human decision flag.
        """
        active_reviewers = list(self.reviewers)

        # Add optional adversarial reviewer for M/L complexity tasks
        if complexity in ("M", "L") and self.optional_reviewers:
            active_reviewers.extend(self.optional_reviewers)

        # No reviewers configured -> auto-approve
        if not active_reviewers:
            return ReviewState(
                rounds_total=0,
                rounds_completed=0,
                reviews=[],
                consensus_score=1.0,
                human_decision_needed=False,
                decision_points=[],
            )

        reviews: list[LLMReview] = []

        for i, reviewer in enumerate(active_reviewers):
            review = await self._call_reviewer(reviewer, task, plan_content)
            reviews.append(review)
            on_progress(i + 1, len(active_reviewers))

        # Synthesize (only if multiple reviews)
        if len(reviews) > 1:
            synthesis = await self._synthesize(reviews, plan_content)
            score = synthesis.score
            disagreements = synthesis.disagreements
        else:
            # Single reviewer: approve/reject is binary
            score = 1.0 if reviews[0].verdict == "approve" else 0.3
            disagreements = (
                reviews[0].suggestions if score < self.threshold else []
            )

        # DB-first: persist each review to history
        if self._history_writer is not None:
            for i, review in enumerate(reviews):
                await self._history_writer.write_review(
                    task_id=task.id,
                    round_number=i + 1,
                    review=review,
                    consensus_score=score if i == len(reviews) - 1 else None,
                    cost_usd=review.cost_usd,
                )

        return ReviewState(
            rounds_total=len(active_reviewers),
            rounds_completed=len(reviews),
            reviews=reviews,
            consensus_score=score,
            human_decision_needed=(score < self.threshold),
            decision_points=disagreements,
        )

    async def _call_claude_cli(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
        max_budget_usd: float = 0.50,
        json_schema: str | None = None,
    ) -> dict:
        """Call the Claude CLI subprocess and return the parsed JSON output.

        Invokes ``claude -p`` with ``--output-format json`` and parses the
        outer JSON wrapper to extract the ``result`` and ``usage`` fields.

        Args:
            prompt: The user prompt to send.
            system_prompt: The system prompt.
            model: Model identifier (e.g., ``"claude-sonnet-4-5"``).
            max_budget_usd: Maximum budget for this CLI call.
            json_schema: Optional JSON schema for structured output.

        Returns:
            Dict with ``result`` (str) and full CLI output for usage extraction.

        Raises:
            RuntimeError: If the subprocess exits with a non-zero code.
        """
        args = [
            "claude", "-p", prompt,
            "--system-prompt", system_prompt,
            "--model", model,
            "--output-format", "json",
            "--no-session-persistence",
            "--max-budget-usd", f"{max_budget_usd:.2f}",
        ]
        if json_schema is not None:
            args.extend(["--json-schema", json_schema])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Claude CLI failed (exit {proc.returncode}): {stderr_text}"
            )

        cli_output = json.loads(stdout_bytes.decode("utf-8"))
        return cli_output

    async def _call_reviewer(
        self,
        reviewer: ReviewerConfig,
        task: Task,
        plan_content: str,
    ) -> LLMReview:
        """Call a reviewer via the Claude CLI.

        Args:
            reviewer: Reviewer configuration (model, focus).
            task: The task being reviewed.
            plan_content: The plan to review.

        Returns:
            LLMReview with parsed verdict, summary, suggestions, and cost_usd.
        """
        system_prompt = self._build_review_prompt(reviewer.focus)

        user_content = (
            f"Task: {task.title}\n"
            f"Task ID: {task.id}\n"
            f"Description: {task.description}\n\n"
            f"Plan:\n{plan_content}"
        )

        cli_output = await self._call_claude_cli(
            prompt=user_content,
            system_prompt=system_prompt,
            model=reviewer.model,
            max_budget_usd=reviewer.max_budget_usd,
            json_schema=_REVIEW_JSON_SCHEMA,
        )

        result_text = cli_output.get("result", "")
        review = self._parse_review(result_text, reviewer)
        review.raw_response = _truncate_raw_response(result_text)
        review.cost_usd = _extract_cost_usd(cli_output, reviewer.model)
        return review

    def _build_review_prompt(self, focus: str) -> str:
        """Generate a focus-area system prompt for a reviewer.

        Args:
            focus: The review focus area (e.g., ``"feasibility_and_edge_cases"``).

        Returns:
            System prompt string.
        """
        return _REVIEW_PROMPTS.get(focus, _DEFAULT_REVIEW_PROMPT)

    def _parse_review(self, text: str, reviewer: ReviewerConfig) -> LLMReview:
        """Parse a Claude CLI result into an LLMReview.

        Args:
            text: The ``result`` text from Claude CLI JSON output.
            reviewer: The reviewer config that generated this response.

        Returns:
            LLMReview with extracted verdict, summary, suggestions.
        """
        try:
            data = json.loads(text)
            verdict = data.get("verdict", "reject")
            summary = data.get("summary", "")
            suggestions = data.get("suggestions", [])
        except (json.JSONDecodeError, KeyError, IndexError):
            logger.warning(
                "Failed to parse review response from %s, treating as reject",
                reviewer.model,
            )
            verdict = "reject"
            summary = text
            suggestions = []

        return LLMReview(
            model=reviewer.model,
            focus=reviewer.focus,
            verdict=verdict,
            summary=summary,
            suggestions=suggestions,
            timestamp=datetime.now(UTC),
        )

    async def _synthesize(
        self,
        reviews: list[LLMReview],
        plan_content: str,
    ) -> SynthesisResult:
        """Synthesize multiple reviews into a consensus score.

        Uses Claude CLI to analyze all reviews and determine a consensus score
        and list of key disagreements.

        Args:
            reviews: List of individual reviewer verdicts.
            plan_content: The original plan being reviewed.

        Returns:
            SynthesisResult with score and disagreements.
        """
        review_texts = "\n---\n".join(
            f"[{r.focus}] ({r.model}): {r.verdict}\n{r.summary}"
            for r in reviews
        )

        synthesis_prompt = (
            f"Given these {len(reviews)} reviews of a task plan, determine:\n"
            f"1. Consensus score (0.0-1.0) where 1.0 = full agreement to approve\n"
            f"2. Key disagreements (if any)\n\n"
            f"Reviews:\n{review_texts}\n\n"
            f"Original plan:\n{plan_content}\n\n"
            'Respond in JSON: {{"score": 0.85, "disagreements": ["..."]}}'
        )

        cli_output = await self._call_claude_cli(
            prompt=synthesis_prompt,
            system_prompt="You are a review synthesis engine.",
            model="claude-sonnet-4-5",
            json_schema=_SYNTHESIS_JSON_SCHEMA,
        )

        return self._parse_synthesis(cli_output.get("result", ""))

    def _parse_synthesis(self, text: str) -> SynthesisResult:
        """Parse synthesis result text into a SynthesisResult.

        Args:
            text: The ``result`` text from Claude CLI JSON output.

        Returns:
            SynthesisResult with score and disagreements.
        """
        try:
            data = json.loads(text)
            score = float(data.get("score", 0.5))
            disagreements = data.get("disagreements", [])
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning("Failed to parse synthesis response, using default score")
            score = 0.5
            disagreements = ["Synthesis parsing failed -- manual review recommended"]

        # Clamp score to [0.0, 1.0]
        score = max(0.0, min(1.0, score))

        return SynthesisResult(score=score, disagreements=disagreements)
