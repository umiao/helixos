"""Review pipeline for HelixOS orchestrator.

Implements LLM-based task plan review per PRD Section 9. Reviews are opt-in
and run as background tasks. Uses Claude CLI (``claude -p``) for LLM calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.history_writer import HistoryWriter
from src.models import LLMReview, ReviewLifecycleState, ReviewState, Task

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
# Updated 2026-03 from https://platform.claude.com/docs/en/about-claude/pricing
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1m, output_per_1m)
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
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
# Process group helpers (same pattern as CodeExecutor / T-P0-30)
# ------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"


def _terminate_review_process(proc: asyncio.subprocess.Process) -> None:
    """Send SIGTERM to the entire process group (or CTRL_BREAK on Windows)."""
    pid = proc.pid
    if pid is None:
        return
    if _IS_WINDOWS:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(pid), signal.SIGTERM)


def _kill_review_process(proc: asyncio.subprocess.Process) -> None:
    """Send SIGKILL to the entire process group (force-kill)."""
    pid = proc.pid
    if pid is None:
        return
    if _IS_WINDOWS:
        with contextlib.suppress(OSError):
            proc.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)


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
        self._timeout_minutes = config.review_timeout_minutes

    async def review_task(
        self,
        task: Task,
        plan_content: str,
        on_progress: Callable[[int, int, str], None],
        complexity: str = "S",
        review_attempt: int = 1,
        human_feedback: list[dict] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> ReviewState:
        """Run the review pipeline for a task plan.

        Args:
            task: The task to review.
            plan_content: The plan text to review.
            on_progress: Callback ``(completed, total, phase)`` for progress
                reporting.  *phase* is a human-readable label such as
                ``"Starting feasibility_and_edge_cases review..."``.
            complexity: Task complexity (``"S"``, ``"M"``, ``"L"``). Adversarial
                reviewer added for M and L tasks.
            review_attempt: Attempt number (1-based). Retries increment this.
            human_feedback: Optional list of previous human feedback entries
                (from ``HistoryWriter.get_human_feedback``).  Each entry has
                ``human_decision``, ``human_reason``, ``review_attempt``, and
                ``timestamp``.  Injected into reviewer prompts as context.
            on_log: Optional per-line callback for real-time streaming of
                Claude CLI output.  Each line from every reviewer subprocess
                is passed to this callback as it arrives.

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
                lifecycle_state=ReviewLifecycleState.APPROVED,
            )

        reviews: list[LLMReview] = []

        def _emit_log(msg: str) -> None:
            if on_log is not None:
                on_log(msg)

        _emit_log("Review started")

        for i, reviewer in enumerate(active_reviewers):
            phase = f"Starting {reviewer.focus} review..."
            on_progress(i, len(active_reviewers), phase)
            _emit_log(phase)
            review = await self._call_reviewer(
                reviewer, task, plan_content,
                human_feedback=human_feedback,
                on_log=on_log,
            )
            reviews.append(review)
            completed_msg = f"Completed {reviewer.focus} review"
            on_progress(i + 1, len(active_reviewers), completed_msg)
            _emit_log(completed_msg)

        # Synthesize (only if multiple reviews)
        if len(reviews) > 1:
            on_progress(len(reviews), len(active_reviewers), "Synthesizing...")
            synthesis = await self._synthesize(reviews, plan_content)
            score = synthesis.score
            disagreements = synthesis.disagreements
        else:
            # Single reviewer: approve=1.0, reject=0.0 (binary)
            score = 1.0 if reviews[0].verdict == "approve" else 0.0
            disagreements = (
                reviews[0].suggestions if score < self.threshold else []
            )

        # Determine lifecycle state from outcome
        lifecycle_state = self._compute_lifecycle_state(
            reviews, score, len(active_reviewers), self.threshold,
        )

        _emit_log(f"Review completed: {lifecycle_state.value}")

        # DB-first: persist each review to history
        # Snapshot the plan at pipeline start (immutable per attempt)
        snapshot = plan_content
        if self._history_writer is not None:
            for i, review in enumerate(reviews):
                # Non-final entries are RUNNING; final entry carries terminal state
                entry_state = (
                    lifecycle_state
                    if i == len(reviews) - 1
                    else ReviewLifecycleState.RUNNING
                )
                await self._history_writer.write_review(
                    task_id=task.id,
                    round_number=i + 1,
                    review=review,
                    consensus_score=score if i == len(reviews) - 1 else None,
                    cost_usd=review.cost_usd,
                    review_attempt=review_attempt,
                    plan_snapshot=snapshot if i == 0 else None,
                    lifecycle_state=entry_state,
                )

        return ReviewState(
            rounds_total=len(active_reviewers),
            rounds_completed=len(reviews),
            reviews=reviews,
            consensus_score=score,
            human_decision_needed=(score < self.threshold),
            decision_points=disagreements,
            lifecycle_state=lifecycle_state,
        )

    @staticmethod
    def _compute_lifecycle_state(
        reviews: list[LLMReview],
        score: float,
        expected_count: int,
        threshold: float = 0.8,
    ) -> ReviewLifecycleState:
        """Compute the terminal lifecycle state from review outcomes.

        Args:
            reviews: Completed reviews.
            score: Consensus score (0.0-1.0).
            expected_count: Number of reviewers that were supposed to run.
            threshold: Consensus score threshold for approval.

        Returns:
            The appropriate terminal ReviewLifecycleState.
        """
        # Partial: fewer reviews completed than expected
        if len(reviews) < expected_count:
            return ReviewLifecycleState.PARTIAL

        # Single reviewer path
        if len(reviews) == 1:
            if reviews[0].verdict == "approve":
                return ReviewLifecycleState.APPROVED
            return ReviewLifecycleState.REJECTED_SINGLE

        # Multi-reviewer path: use consensus score vs threshold
        if score >= threshold:
            return ReviewLifecycleState.APPROVED
        return ReviewLifecycleState.REJECTED_CONSENSUS

    async def _call_claude_cli(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
        max_budget_usd: float = 0.50,
        json_schema: str | None = None,
        on_log: Callable[[str], None] | None = None,
        heartbeat_seconds: int = 30,
    ) -> dict:
        """Call the Claude CLI subprocess and return the parsed JSON output.

        Invokes ``claude -p`` with ``--output-format json`` and streams
        stdout line-by-line via *on_log* for real-time progress.  Parses
        the assembled output as JSON to extract ``result`` and ``usage``.

        Uses process group isolation (same pattern as CodeExecutor / T-P0-30)
        and ``asyncio.timeout`` to enforce ``review_timeout_minutes``.

        Args:
            prompt: The user prompt to send.
            system_prompt: The system prompt.
            model: Model identifier (e.g., ``"claude-sonnet-4-5"``).
            max_budget_usd: Maximum budget for this CLI call.
            json_schema: Optional JSON schema for structured output.
            on_log: Optional per-line callback for real-time streaming.
            heartbeat_seconds: Seconds without output before emitting a
                heartbeat via *on_log*.  Set to 0 to disable.

        Returns:
            Dict with ``result`` (str) and full CLI output for usage extraction.

        Raises:
            RuntimeError: If the subprocess exits with a non-zero code or
                times out.
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

        # Platform-specific process group flags
        kwargs: dict[str, object] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if _IS_WINDOWS:
            import subprocess as _subprocess
            kwargs["creationflags"] = (
                _subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        else:
            kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(
            *args,
            **kwargs,  # type: ignore[arg-type]
        )

        timeout_seconds = self._timeout_minutes * 60 if self._timeout_minutes > 0 else None
        log_lines: list[str] = []
        last_output_time = time.monotonic()

        def _emit(line: str) -> None:
            if on_log is not None:
                on_log(line)

        try:
            async with asyncio.timeout(timeout_seconds):
                assert proc.stdout is not None
                while True:
                    try:
                        if heartbeat_seconds > 0:
                            raw_line = await asyncio.wait_for(
                                proc.stdout.readline(),
                                timeout=heartbeat_seconds,
                            )
                        else:
                            raw_line = await proc.stdout.readline()
                    except TimeoutError:
                        elapsed = int(time.monotonic() - last_output_time)
                        _emit(
                            f"[PROGRESS] heartbeat -- no output for {elapsed}s"
                        )
                        continue

                    if not raw_line:  # EOF
                        break

                    decoded = raw_line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        log_lines.append(decoded)
                        _emit(decoded)
                        last_output_time = time.monotonic()

                await proc.wait()
        except TimeoutError:
            logger.warning(
                "Review subprocess timed out after %d minutes (model=%s), "
                "killing process group",
                self._timeout_minutes, model,
            )
            _terminate_review_process(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                _kill_review_process(proc)
                await proc.wait()
            raise RuntimeError(
                f"Review subprocess timed out after {self._timeout_minutes} "
                f"minutes (model={model})"
            ) from None

        if proc.returncode != 0:
            stderr_bytes = b""
            if proc.stderr is not None:
                stderr_bytes = await proc.stderr.read()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Claude CLI failed (exit {proc.returncode}): {stderr_text}"
            )

        # Reassemble all stdout lines and parse the JSON result
        full_output = "\n".join(log_lines)
        try:
            cli_output = json.loads(full_output)
        except json.JSONDecodeError:
            # Some CLIs emit progress text before the final JSON blob
            cli_output = {}
            for line in reversed(log_lines):
                try:
                    cli_output = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        return cli_output

    async def _call_reviewer(
        self,
        reviewer: ReviewerConfig,
        task: Task,
        plan_content: str,
        human_feedback: list[dict] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> LLMReview:
        """Call a reviewer via the Claude CLI.

        Args:
            reviewer: Reviewer configuration (model, focus).
            task: The task being reviewed.
            plan_content: The plan to review.
            human_feedback: Optional list of previous human feedback entries.
            on_log: Optional per-line callback for real-time streaming.

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

        # Inject previous human feedback into the prompt
        if human_feedback:
            feedback_lines = []
            for fb in human_feedback:
                decision = fb.get("human_decision", "")
                reason = fb.get("human_reason", "")
                attempt = fb.get("review_attempt", "?")
                line = f"- [Attempt {attempt}] {decision.upper()}"
                if reason:
                    line += f": {reason}"
                feedback_lines.append(line)
            user_content += (
                "\n\n--- Previous human feedback ---\n"
                + "\n".join(feedback_lines)
                + "\n--- End of feedback ---\n"
                "\nPlease address the above feedback in your review."
            )

        cli_output = await self._call_claude_cli(
            prompt=user_content,
            system_prompt=system_prompt,
            model=reviewer.model,
            max_budget_usd=reviewer.max_budget_usd,
            json_schema=_REVIEW_JSON_SCHEMA,
            on_log=on_log,
        )

        result_text = cli_output.get("result", "")
        review = self._parse_review(result_text, reviewer)

        # Build raw_response from explicit CLI fields (not the full
        # cli_output blob).  This decouples the DB schema from the CLI
        # contract and ensures raw_response contains metadata (model,
        # usage, session_id) NOT already present in summary/suggestions.
        raw_response_dict = {
            "model": cli_output.get("model"),
            "usage": cli_output.get("usage"),
            "result": cli_output.get("result"),
            "session_id": cli_output.get("session_id"),
        }
        review.raw_response = _truncate_raw_response(
            json.dumps(raw_response_dict, indent=2)
        )
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
