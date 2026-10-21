"""Agent variants shared across Colosseum experiments."""

from __future__ import annotations

from typing import Any

from terrarium.agents.base import BaseAgent


def _ends_with_model_turn(context: list[Any]) -> bool:
    """True when the last history entry is a Gemini ``model`` turn.

    Only the Gemini client appends ``role="model"`` entries (as ``types.Content``);
    other providers use ``assistant`` objects/dicts, so this never matches for them.
    """
    if not context:
        return False
    last = context[-1]
    role = last.get("role") if isinstance(last, dict) else getattr(last, "role", None)
    return role == "model"


class TurnEndingAgent(BaseAgent):
    """BaseAgent that ends its multi-step loop once the model replies without a tool call.

    ``BaseAgent._multi_step_response_generation`` keeps calling the model until an
    environment tool commits state or ``max_conversation_steps`` is reached. In phases
    that only expose ``post_message`` (e.g. Jira planning), Gemini runs in AUTO
    function-calling mode and may finish with a text-only reply. The client appends that
    reply as a ``model`` turn without a function response, so the next request ends with
    a model turn and Gemini rejects it with
    ``400 INVALID_ARGUMENT: Requests ending with a model turn are not supported``.

    A text-only reply means the model has nothing further to do this turn, so we stop the
    loop instead of fabricating a user turn or dropping the model's reply from history.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client_process_tool_calls = self.client.process_tool_calls
        self.client.process_tool_calls = self._process_tool_calls_then_end_turn

    async def _process_tool_calls_then_end_turn(
        self, response: Any, context: list[Any], execute_tool_callback: Any
    ) -> tuple[int, list[Any], list[str]]:
        executed, context, step_tools = await self._client_process_tool_calls(
            response, context, execute_tool_callback
        )
        if executed == 0 and _ends_with_model_turn(context):
            # The flag is reset at the start of every loop step and is the loop's only
            # early exit; setting it here ends the turn without another model call.
            self._env_state_committed = True
        return executed, context, step_tools
