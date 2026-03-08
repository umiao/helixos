"""Review pipeline for HelixOS orchestrator.

Implements LLM-based task plan review per PRD Section 9. Reviews are opt-in
and run as background tasks. Uses Claude Agent SDK via ``sdk_adapter`` for
LLM calls. Migrated from raw subprocess in T-P1-89.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.executors.code_executor import _LazyFileWriter
from src.history_writer import HistoryWriter
from src.models import LLMReview, ReviewLifecycleState, ReviewState, Task
from src.prompt_loader import load_prompt, render_prompt
from src.sdk_adapter import (
    ClaudeEvent,
    ClaudeEventType,
    QueryOptions,
    collect_turns,
    run_claude_query,
)
from src.session_context_loader import get_session_context

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Internal models
# ------------------------------------------------------------------


class ReviewResult(BaseModel):
    """Validates review JSON matches --json-schema contract."""

    verdict: Literal["approve", "reject"]
    summary: str
    suggestions: list[str]


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

_REVIEW_CONVENTIONS_CONTEXT = render_prompt(
    "review_conventions_context",
    task_schema_context=load_prompt("task_schema_context"),
    project_rules_context=load_prompt("project_rules_context"),
)

_REVIEW_PROMPTS: dict[str, str] = {
    "feasibility_and_edge_cases": render_prompt(
        "review_feasibility",
        review_conventions_context=_REVIEW_CONVENTIONS_CONTEXT,
    ),
    "adversarial_red_team": render_prompt(
        "review_adversarial",
        review_conventions_context=_REVIEW_CONVENTIONS_CONTEXT,
    ),
}

_DEFAULT_REVIEW_PROMPT = render_prompt(
    "review_default",
    review_conventions_context=_REVIEW_CONVENTIONS_CONTEXT,
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
        stream_log_dir: Path | None = None,
    ) -> None:
        """Initialize the review pipeline.

        Args:
            config: Review pipeline configuration (reviewers list).
            threshold: Consensus score threshold for auto-approval.
            history_writer: Optional DB-first review history writer.
            stream_log_dir: Directory for JSONL stream log persistence.
                When set, each CLI call writes ``review_stream_*.jsonl``
                and ``review_raw_*.log`` files under ``{stream_log_dir}/{task_id}/``.
        """
        self.reviewers = [r for r in config.reviewers if r.required]
        self.optional_reviewers = [r for r in config.reviewers if not r.required]
        self.threshold = threshold
        self._history_writer = history_writer
        self._timeout_minutes = config.review_timeout_minutes
        self._stream_log_dir = stream_log_dir

    async def review_task(
        self,
        task: Task,
        plan_content: str,
        on_progress: Callable[[int, int, str], None],
        complexity: str = "S",
        review_attempt: int = 1,
        human_feedback: list[dict] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_raw_artifact: Callable[[str], Awaitable[None]] | None = None,
        on_stream_event: Callable[[dict], None] | None = None,
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
            on_stream_event: Optional callback for parsed stream-json events.
                Each parsed JSON dict is passed to this callback for SSE
                emission and ConversationView updates.

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
                on_raw_artifact=on_raw_artifact,
                on_stream_event=on_stream_event,
            )
            reviews.append(review)
            completed_msg = f"Completed {reviewer.focus} review"
            on_progress(i + 1, len(active_reviewers), completed_msg)
            _emit_log(completed_msg)

        # Synthesize (only if multiple reviews)
        if len(reviews) > 1:
            on_progress(len(reviews), len(active_reviewers), "Synthesizing...")
            synthesis = await self._synthesize(
                reviews, plan_content, task_id=task.id,
                on_stream_event=on_stream_event,
            )
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

    async def _call_claude_sdk(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
        max_budget_usd: float | None = None,
        json_schema: str | None = None,
        on_log: Callable[[str], None] | None = None,
        heartbeat_seconds: int = 30,
        on_raw_artifact: Callable[[str], Awaitable[None]] | None = None,
        on_stream_event: Callable[[dict], None] | None = None,
        task_id: str | None = None,
    ) -> tuple[dict, list[ClaudeEvent]]:
        """Call Claude via the Agent SDK and return parsed output + raw events.

        Uses ``run_claude_query()`` with a producer-task + queue pattern for
        heartbeat support.  Emits events via *on_stream_event* for SSE and
        persists to JSONL files.

        Args:
            prompt: The user prompt to send.
            system_prompt: The system prompt.
            model: Model identifier (e.g., ``"claude-sonnet-4-5"``).
            max_budget_usd: Maximum budget for this call.
            json_schema: Optional JSON schema for structured output.
            on_log: Optional per-line callback for real-time streaming.
            heartbeat_seconds: Seconds without output before emitting a
                heartbeat via *on_log*.  Set to 0 to disable.
            on_raw_artifact: Optional async callback to persist raw event output.
            on_stream_event: Optional callback for parsed stream-json events.
            task_id: Optional task ID for JSONL log file naming.

        Returns:
            Tuple of (result_dict, all_events) where result_dict contains
            ``structured_output`` / ``result`` and metadata, and all_events
            is the list of raw ``ClaudeEvent`` objects for turn reconstruction.

        Raises:
            RuntimeError: If the SDK returns an error or times out.
        """
        # Inject session context into system prompt (replaces SessionStart
        # hook which is not available as an SDK hook type).
        session_ctx = get_session_context()
        enriched_prompt = system_prompt + "\n\n" + session_ctx

        options = QueryOptions(
            model=model,
            system_prompt=enriched_prompt,
            max_budget_usd=max_budget_usd,
            json_schema=json_schema,
            # Disable CLI hooks (block_dangerous, secret_guard, etc.) for
            # review sessions.  Session context is injected above instead.
            setting_sources=[],
        )

        # -- JSONL log persistence (lazy: files created on first write) --
        jsonl_file: _LazyFileWriter | None = None
        raw_file: _LazyFileWriter | None = None
        if task_id is not None and self._stream_log_dir is not None:
            log_dir = self._stream_log_dir / task_id.replace(":", "_")
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            jsonl_file = _LazyFileWriter(log_dir / f"review_stream_{ts}.jsonl")
            raw_file = _LazyFileWriter(log_dir / f"review_raw_{ts}.log")

        timeout_seconds = self._timeout_minutes * 60 if self._timeout_minutes > 0 else None
        all_events: list[ClaudeEvent] = []
        all_event_dicts: list[dict] = []
        cli_output: dict[str, Any] = {}
        last_output_time = time.monotonic()
        has_error = False
        error_detail = ""

        def _emit(line: str) -> None:
            if on_log is not None:
                on_log(line)

        try:
            async with asyncio.timeout(timeout_seconds):
                event_queue: asyncio.Queue[object] = asyncio.Queue()
                _sentinel = object()

                async def _produce() -> None:
                    async for ev in run_claude_query(prompt, options):
                        await event_queue.put(ev)
                    await event_queue.put(_sentinel)

                producer = asyncio.create_task(_produce())
                try:
                    while True:
                        try:
                            hb_timeout = (
                                heartbeat_seconds if heartbeat_seconds > 0 else None
                            )
                            item = await asyncio.wait_for(
                                event_queue.get(), timeout=hb_timeout,
                            )
                        except TimeoutError:
                            elapsed = int(time.monotonic() - last_output_time)
                            _emit(
                                f"[PROGRESS] heartbeat -- no output for {elapsed}s"
                            )
                            continue

                        if item is _sentinel:
                            break

                        event: ClaudeEvent = item  # type: ignore[assignment]
                        last_output_time = time.monotonic()
                        all_events.append(event)
                        event_dict = event.model_dump(exclude_none=True)
                        all_event_dicts.append(event_dict)

                        # JSONL persistence
                        if jsonl_file is not None:
                            jsonl_file.write(
                                json.dumps(event_dict, ensure_ascii=False) + "\n"
                            )
                            jsonl_file.flush()
                        if raw_file is not None:
                            raw_file.write(
                                json.dumps(event_dict, ensure_ascii=False) + "\n"
                            )
                            raw_file.flush()

                        # Stream event callback
                        if on_stream_event is not None:
                            on_stream_event(event_dict)

                        # Process by event type
                        if event.type == ClaudeEventType.RESULT:
                            cli_output = {
                                "type": "result",
                                "structured_output": event.structured_output,
                                "result": event.result_text,
                                "model": event.model,
                                "usage": event.usage,
                                "session_id": event.session_id,
                                "cost_usd": event.cost_usd,
                                "duration_ms": event.duration_ms,
                                "num_turns": event.num_turns,
                            }
                            _emit("[DONE]")
                        elif event.type == ClaudeEventType.ERROR:
                            has_error = True
                            error_detail = event.error_message or "Unknown error"
                        elif event.type == ClaudeEventType.TEXT:
                            if event.text:
                                _emit(event.text)
                        elif event.type == ClaudeEventType.TOOL_USE:
                            input_str = json.dumps(event.tool_input or {})[:200]
                            _emit(f"[TOOL] {event.tool_name}({input_str})")
                        elif event.type == ClaudeEventType.TOOL_RESULT:
                            content = (event.tool_result_content or "")[:200]
                            _emit(f"[RESULT] {content}")
                        elif event.type == ClaudeEventType.INIT:
                            _emit(f"[INIT] session={event.session_id}")
                finally:
                    producer.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await producer
        except TimeoutError:
            logger.warning(
                "Review SDK call timed out after %d minutes (model=%s)",
                self._timeout_minutes, model,
            )
            raise RuntimeError(
                f"Review SDK call timed out after {self._timeout_minutes} "
                f"minutes (model={model})"
            ) from None
        finally:
            if jsonl_file is not None:
                jsonl_file.close()
            if raw_file is not None:
                raw_file.close()

        # PERSIST-FIRST: save raw output BEFORE error check.
        full_output = "\n".join(
            json.dumps(e, ensure_ascii=False) for e in all_event_dicts
        )
        if on_raw_artifact is not None:
            try:
                await on_raw_artifact(full_output)
            except Exception:
                logger.warning(
                    "Failed to persist raw artifact for review, continuing",
                    exc_info=True,
                )

        if has_error:
            raise RuntimeError(f"Claude SDK error: {error_detail}")

        if not cli_output:
            logger.warning(
                "No result event from SDK (%d events). Raw: %.500s",
                len(all_event_dicts), full_output,
            )

        return cli_output, all_events

    async def _call_reviewer(
        self,
        reviewer: ReviewerConfig,
        task: Task,
        plan_content: str,
        human_feedback: list[dict] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_raw_artifact: Callable[[str], Awaitable[None]] | None = None,
        on_stream_event: Callable[[dict], None] | None = None,
    ) -> LLMReview:
        """Call a reviewer via the Claude Agent SDK.

        Args:
            reviewer: Reviewer configuration (model, focus).
            task: The task being reviewed.
            plan_content: The plan to review.
            human_feedback: Optional list of previous human feedback entries.
            on_log: Optional per-line callback for real-time streaming.
            on_raw_artifact: Optional callback to persist full raw output.
            on_stream_event: Optional callback for parsed stream-json events.

        Returns:
            LLMReview with parsed verdict, summary, suggestions, cost_usd,
            and conversation_turns.
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

        cli_output, all_events = await self._call_claude_sdk(
            prompt=user_content,
            system_prompt=system_prompt,
            model=reviewer.model,
            max_budget_usd=reviewer.max_budget_usd,
            json_schema=_REVIEW_JSON_SCHEMA,
            on_log=on_log,
            on_raw_artifact=on_raw_artifact,
            on_stream_event=on_stream_event,
            task_id=task.id,
        )

        # structured_output is a dict when --json-schema was used; fall back to result
        result_data = cli_output.get("structured_output") or cli_output.get("result", "")
        review = self._parse_review(result_data, reviewer)

        # Reconstruct conversation turns from raw events
        turns, sdk_result = await collect_turns(_events_to_aiter(all_events))
        conversation_turns = [t.model_dump() for t in turns]
        review.conversation_turns = conversation_turns

        # Extract structured summary from turns
        review.conversation_summary = _extract_conversation_summary(turns)

        # Build raw_response from explicit fields (not the full blob).
        # This decouples the DB schema from the SDK contract and ensures
        # raw_response contains metadata (model, usage, session_id) NOT
        # already present in summary/suggestions.
        raw_response_dict = {
            "model": cli_output.get("model"),
            "usage": cli_output.get("usage"),
            "result": cli_output.get("structured_output") or cli_output.get("result"),
            "session_id": cli_output.get("session_id"),
        }
        review.raw_response = _truncate_raw_response(
            json.dumps(raw_response_dict, indent=2)
        )

        # Use SDK-reported cost if available, fall back to token-based estimate
        review.cost_usd = sdk_result.cost_usd or _extract_cost_usd(
            cli_output, reviewer.model,
        )
        return review

    def _build_review_prompt(self, focus: str) -> str:
        """Generate a focus-area system prompt for a reviewer.

        Args:
            focus: The review focus area (e.g., ``"feasibility_and_edge_cases"``).

        Returns:
            System prompt string.
        """
        return _REVIEW_PROMPTS.get(focus, _DEFAULT_REVIEW_PROMPT)

    def _parse_review(self, text: str | dict, reviewer: ReviewerConfig) -> LLMReview:
        """Parse a Claude CLI result into an LLMReview.

        Args:
            text: The ``structured_output`` (dict) or ``result`` (str) field
                from Claude CLI JSON output.
            reviewer: The reviewer config that generated this response.

        Returns:
            LLMReview with extracted verdict, summary, suggestions.
        """
        try:
            data = text if isinstance(text, dict) else json.loads(text)
            result = ReviewResult.model_validate(data)
            verdict = result.verdict
            summary = result.summary
            suggestions = result.suggestions
        except (json.JSONDecodeError, ValidationError, KeyError, IndexError) as exc:
            raw_repr = str(text)
            logger.warning(
                "Failed to parse review response from %s: %s. Raw (%d chars): %.500s",
                reviewer.model, exc, len(raw_repr), raw_repr,
            )
            verdict = "reject"
            summary = raw_repr if isinstance(text, str) else str(text)
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
        task_id: str | None = None,
        on_stream_event: Callable[[dict], None] | None = None,
    ) -> SynthesisResult:
        """Synthesize multiple reviews into a consensus score.

        Uses Claude CLI to analyze all reviews and determine a consensus score
        and list of key disagreements.

        Args:
            reviews: List of individual reviewer verdicts.
            plan_content: The original plan being reviewed.
            task_id: Optional task ID for JSONL log file naming.
            on_stream_event: Optional callback for parsed stream-json events.

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

        cli_output, _events = await self._call_claude_sdk(
            prompt=synthesis_prompt,
            system_prompt="You are a review synthesis engine.",
            model="claude-opus-4-6",
            json_schema=_SYNTHESIS_JSON_SCHEMA,
            on_stream_event=on_stream_event,
            task_id=task_id,
        )

        # structured_output is a dict when --json-schema was used; fall back to result
        synthesis_data = cli_output.get("structured_output") or cli_output.get("result", "")
        return self._parse_synthesis(synthesis_data)

    def _parse_synthesis(self, text: str | dict) -> SynthesisResult:
        """Parse synthesis result text into a SynthesisResult.

        Args:
            text: The ``structured_output`` (dict) or ``result`` (str) field
                from Claude CLI JSON output.

        Returns:
            SynthesisResult with score and disagreements.
        """
        try:
            data = text if isinstance(text, dict) else json.loads(text)
            result = SynthesisResult.model_validate(data)
            score = result.score
            disagreements = result.disagreements
        except (json.JSONDecodeError, ValidationError, KeyError, ValueError) as exc:
            raw_repr = str(text)
            logger.warning(
                "Failed to parse synthesis response: %s. Raw (%d chars): %.500s",
                exc, len(raw_repr), raw_repr,
            )
            score = 0.5
            disagreements = ["Synthesis parsing failed -- manual review recommended"]

        # Clamp score to [0.0, 1.0]
        score = max(0.0, min(1.0, score))

        return SynthesisResult(score=score, disagreements=disagreements)


# ------------------------------------------------------------------
# Helpers for conversation turn reconstruction
# ------------------------------------------------------------------


async def _events_to_aiter(
    events: list[ClaudeEvent],
) -> AsyncIterator[ClaudeEvent]:
    """Convert a list of ClaudeEvent objects to an async iterator.

    This allows reuse of ``collect_turns()`` which expects an async iterator.

    Args:
        events: List of ClaudeEvent objects.

    Yields:
        Each ClaudeEvent in order.
    """
    for event in events:
        yield event


def _extract_conversation_summary(
    turns: list[Any],
) -> dict[str, Any]:
    """Extract a structured summary from conversation turns.

    Post-processes ``AssistantTurn`` objects to produce a summary dict with
    findings, actions taken, and conclusion.

    Args:
        turns: List of ``AssistantTurn`` objects from ``collect_turns()``.

    Returns:
        Dict with ``findings`` (list[str]), ``actions_taken`` (list[str]),
        and ``conclusion`` (str).
    """
    findings: list[str] = []
    actions_taken: list[str] = []
    conclusion = ""

    for turn in turns:
        # Extract tool actions as "actions taken"
        for action in getattr(turn, "tool_actions", []):
            action_name = getattr(action, "name", "")
            if action_name:
                actions_taken.append(action_name)

        # Extract text content as findings / conclusion
        text = getattr(turn, "text", "").strip()
        if text:
            findings.append(text)

    # Last non-empty text is the conclusion
    if findings:
        conclusion = findings[-1]

    return {
        "findings": findings,
        "actions_taken": actions_taken,
        "conclusion": conclusion,
    }
