"""Stage 6 - the one step that needs a language model.

Everything the model receives has already been parsed, screened, extracted and
masked by the stages before it, and everything it returns is checked by the
stages after it. Its job is narrow: read a prepared thread and produce one JSON
object.

Three habits keep that job safe:

* the model is given a JSON schema and must answer with it, so free-form text
  never reaches the application;
* the email content is fenced between markers and labelled as untrusted data,
  and any marker inside the content itself is neutralised so it cannot close
  the fence early;
* the model has no tools, no network access and no way to act - the worst a
  successful prompt injection can achieve is a misleading summary, which the
  guardrails then flag.

Long threads are summarised part by part and then combined, always with the
newest message included in full because it decides the next step.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from app import prompts
from app.config import Settings
from app.models import EmailThread, Summary, ThreadFacts

BEGIN_MARKER = "BEGIN UNTRUSTED EMAIL CONTENT"
END_MARKER = "END UNTRUSTED EMAIL CONTENT"

# Rough but stable: roughly four characters per token for English prose. Used
# only to decide when to chunk, so an approximation is fine.
_CHARS_PER_TOKEN = 4
# Leave room for the prompt scaffolding and the model's own answer.
_CONTEXT_SAFETY_FRACTION = 0.6


class ModelUnavailableError(RuntimeError):
    """The local model could not be reached. Always carries a fix for the user."""


class ModelResponseError(RuntimeError):
    """The model replied, but never with something matching the schema."""


@dataclass(frozen=True)
class SummaryResult:
    """A validated summary plus which model produced it."""

    summary: Summary
    model_used: str


class OllamaClient:
    """Minimal transport for a locally running Ollama instance.

    Only two endpoints are used, and the HTTP client is injected so tests can
    drive the whole stage without a model running.
    """

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = http_client or httpx.Client(
            base_url=settings.ollama_host, timeout=settings.request_timeout_seconds
        )

    def is_available(self) -> bool:
        """Report whether the model service is reachable, without raising."""
        try:
            response = self._client.get("/api/tags", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    def chat(self, *, system: str, user: str, schema: dict) -> tuple[str, str]:
        """Send one prompt and return ``(content, model_name)``."""
        payload = {
            "model": self._settings.model_name,
            "stream": False,
            # Qwen models reason out loud unless told not to; the schema leaves
            # no room for it and it only costs time.
            "think": False,
            "format": schema,
            "options": {
                "temperature": self._settings.model_temperature,
                "seed": self._settings.model_seed,
                "num_ctx": self._settings.model_context_tokens,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

        try:
            response = self._client.post(
                "/api/chat", json=payload, timeout=self._settings.request_timeout_seconds
            )
        except httpx.TimeoutException as exc:
            raise ModelUnavailableError(
                "the local model took too long to answer; try a smaller model "
                "or raise EMAILASSIST_REQUEST_TIMEOUT_SECONDS"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(
                f"could not reach Ollama at {self._settings.ollama_host}; " "start it and try again"
            ) from exc

        self._raise_for_status(response)

        body = response.json()
        content = body.get("message", {}).get("content", "")
        return content, body.get("model", self._settings.model_name)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == httpx.codes.OK:
            return

        if response.status_code == httpx.codes.NOT_FOUND:
            raise ModelUnavailableError(
                f"the model '{self._settings.model_name}' is not installed; "
                f"run: ollama pull {self._settings.model_name}"
            )

        raise ModelUnavailableError(
            f"the local model returned an error (HTTP {response.status_code})"
        )


class Summarizer:
    """Turns a prepared thread into a validated :class:`Summary`."""

    def __init__(self, client: OllamaClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        # A fresh marker per instance: if it ever appears in the output, the
        # model has been talked into repeating its instructions.
        self.canary = f"session-{secrets.token_hex(8)}"

    def summarize(
        self,
        thread: EmailThread,
        facts: ThreadFacts,
        warnings: list[str] | None = None,
    ) -> SummaryResult:
        """Summarise a thread, chunking it first if it cannot fit in context."""
        content = self._render_thread(thread)

        if self._fits_in_context(content):
            user_prompt = prompts.render(
                "summarize",
                subject=thread.subject,
                facts=_render_facts(facts, warnings),
                content=fence(content),
            )
        else:
            user_prompt = self._build_reduce_prompt(thread, facts, warnings)

        return self._ask(user_prompt)

    # -- prompting ------------------------------------------------------

    def _ask(self, user_prompt: str) -> SummaryResult:
        """Send a prompt, retrying once with the validation error explained."""
        system_prompt = prompts.render("system", canary=self.canary)
        schema = Summary.model_json_schema()
        last_error = ""

        for attempt in range(self._settings.max_model_retries + 2):
            prompt = user_prompt if attempt == 0 else _retry_prompt(user_prompt, last_error)
            content, model_name = self._client.chat(
                system=system_prompt, user=prompt, schema=schema
            )

            try:
                return SummaryResult(summary=_parse_summary(content), model_used=model_name)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = _short_error(exc)

        raise ModelResponseError(
            f"the model did not return a usable answer after "
            f"{self._settings.max_model_retries + 2} attempts: {last_error}"
        )

    def _build_reduce_prompt(
        self, thread: EmailThread, facts: ThreadFacts, warnings: list[str] | None = None
    ) -> str:
        """Summarise the thread in parts, then ask for one combined answer."""
        older = thread.messages[:-1]
        newest = thread.messages[-1]
        chunks = self._chunk([_render_message(message) for message in older])

        part_summaries = []
        for index, chunk in enumerate(chunks, start=1):
            content, _ = self._client.chat(
                system=prompts.render("system", canary=self.canary),
                user=prompts.render("chunk", index=index, total=len(chunks), content=fence(chunk)),
                schema=Summary.model_json_schema(),
            )
            part_summaries.append(f"Part {index}: {_readable_part(content)}")

        return prompts.render(
            "reduce",
            subject=thread.subject,
            facts=_render_facts(facts, warnings),
            parts="\n".join(part_summaries),
            content=fence(_render_message(newest)),
        )

    # -- sizing ---------------------------------------------------------

    def _budget_chars(self) -> int:
        return int(
            self._settings.model_context_tokens * _CHARS_PER_TOKEN * _CONTEXT_SAFETY_FRACTION
        )

    def _fits_in_context(self, content: str) -> bool:
        return len(content) <= self._budget_chars()

    def _chunk(self, rendered_messages: list[str]) -> list[str]:
        """Group messages into chunks that each fit the context budget."""
        budget = self._budget_chars()
        chunks: list[str] = []
        current: list[str] = []
        current_size = 0

        for rendered in rendered_messages:
            if current and current_size + len(rendered) > budget:
                chunks.append("\n\n".join(current))
                current, current_size = [], 0
            current.append(rendered[:budget])
            current_size += len(rendered)

        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _render_thread(self, thread: EmailThread) -> str:
        blocks = [_render_message(message) for message in thread.messages]
        blocks += [
            f"[Attachment: {attachment.filename}]\n{attachment.extracted_text}"
            for attachment in thread.attachments
            if attachment.has_text
        ]
        return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fence(content: str) -> str:
    """Neutralise any fence markers inside untrusted content.

    Without this, an email containing the closing marker could end the quoted
    block early and have the rest of its text read as instructions.
    """
    for marker in (BEGIN_MARKER, END_MARKER):
        content = content.replace(marker, marker.replace(" ", "․"))
    return content


def _render_message(message) -> str:
    when = message.sent_at.date().isoformat() if message.sent_at else "date unknown"
    return f"From: {message.sender} ({when})\n{message.body}"


def _render_facts(facts: ThreadFacts, warnings: list[str] | None = None) -> str:
    lines = []
    if facts.today:
        lines.append(f"- today's date: {facts.today}")
    if facts.owner_address:
        lines.append(f'- you are writing for {facts.owner_address}; that address is "me"')
    else:
        lines.append('- the mailbox owner is not identified; infer who "me" is from context')
    lines += [
        f"- participants: {', '.join(facts.participants) or 'unknown'}",
        f"- messages in thread: {facts.message_count}",
        f"- last message written by: {facts.last_sender or 'unknown'}",
    ]
    if facts.days_since_last_message is not None:
        lines.append(f"- days since the last message: {facts.days_since_last_message}")
    if facts.dates_mentioned:
        lines.append(f"- dates found in the text: {', '.join(facts.dates_mentioned)}")
    if facts.open_questions:
        lines.append("- questions still unanswered:")
        lines += [f"    * {question}" for question in facts.open_questions]

    # The injection detector runs before the model. Telling the model what it
    # found makes the model far less likely to act on the text it found.
    for warning in warnings or []:
        lines.append(f"- SECURITY WARNING: {warning}")

    return "\n".join(lines)


def _parse_summary(content: str) -> Summary:
    return Summary.model_validate(json.loads(content))


def _retry_prompt(original: str, error: str) -> str:
    return (
        f"{original}\n\n"
        f"Your previous answer could not be used: {error}\n"
        f"Answer again with valid JSON matching the schema exactly, and nothing else."
    )


def _readable_part(content: str) -> str:
    """Turn a part-summary response into a line of text for the combine step."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content.strip()[:1000]

    if isinstance(parsed, dict):
        pieces = [str(parsed.get("summary", "")).strip()]
        pieces += [str(point) for point in parsed.get("key_points", []) or []]
        return " ".join(piece for piece in pieces if piece)[:1000]
    return str(parsed)[:1000]


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "answer"
        return f"{location}: {first['msg']}"
    return str(exc).splitlines()[0][:120]
