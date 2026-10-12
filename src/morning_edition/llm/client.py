import copy
import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger("morning_edition")


class LLMError(Exception):
    """Any failure to get a valid structured answer from Claude."""


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema reshaped for structured outputs: refs inlined,
    every object closed (additionalProperties false) with all fields required."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node, is_props=False):
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node
        if not is_props and "$ref" in node:
            return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
        out = {}
        for key, value in node.items():
            if not is_props and key in ("title", "default"):
                continue
            out[key] = resolve(value, is_props=(key == "properties" and not is_props))
        if not is_props and out.get("type") == "object":
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    return resolve(schema)


class LLM:
    def __init__(self, model: str, effort: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()
        self.errors: list[str] = []  # one entry per failed call, so the pipeline can surface them

    def ask_json(self, system: str, user: str, schema: type[T], max_tokens: int = 16000) -> T:
        try:
            return self._ask_json(system, user, schema, max_tokens)
        except LLMError as exc:
            self._record(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - any SDK failure is an LLMError, never a crash
            self._record(f"{type(exc).__name__}: {exc}")
            raise LLMError(f"Claude call failed: {type(exc).__name__}: {exc}") from exc

    def _record(self, message: str) -> None:
        self.errors.append(message)
        log.warning("Claude call failed: %s", message)

    def _ask_json(self, system: str, user: str, schema: type[T], max_tokens: int) -> T:
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": strict_schema(schema)},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Claude API error: {exc}") from exc
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise LLMError(f"Claude declined ({getattr(details, 'category', None)})")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Claude hit max_tokens before finishing")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise LLMError("Claude returned no text block")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMError(f"Claude returned JSON that does not match the schema: {exc}") from exc
