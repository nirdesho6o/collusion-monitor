"""Regression test: a text-only Gemini reply must end the agent turn.

Run with: python -m unittest tests.test_turn_ending_agent
"""

import asyncio
import unittest

from google.genai import types

from experiments.common.agents import TurnEndingAgent


class _FakeGeminiClient:
    """Mimics GeminiClient's history handling: post_message call, then a text-only reply."""

    def __init__(self):
        self.requests = []
        self._replies = [
            types.Content(
                role="model",
                parts=[types.Part(function_call=types.FunctionCall(name="post_message", args={"message": "hi"}))],
            ),
            types.Content(role="model", parts=[types.Part(text="Done.")]),
        ]

    def init_context(self, system_prompt, user_prompt):
        return [{"role": "user", "parts": [{"text": user_prompt}]}]

    def generate_response(self, input, params):
        roles = [m["role"] if isinstance(m, dict) else m.role for m in input]
        if roles[-1] == "model":
            raise AssertionError("400 INVALID_ARGUMENT: Requests ending with a model turn are not supported.")
        self.requests.append(roles)
        content = self._replies[len(self.requests) - 1]
        return content, "".join(p.text or "" for p in content.parts)

    @staticmethod
    def get_usage(response, usage):
        return usage

    async def process_tool_calls(self, response, context, execute_tool_callback):
        context.append(response)
        executed = 0
        for part in response.parts:
            if part.function_call:
                context.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(name=part.function_call.name, response={"result": {}}))],
                    )
                )
                executed += 1
        return executed, context, []


class TurnEndingAgentTest(unittest.TestCase):
    def test_text_only_reply_ends_turn_without_model_ending_request(self):
        client = _FakeGeminiClient()
        agent = TurnEndingAgent(client, "Agent1", "fake", 3, environment_name="JiraTicketEnvironment")
        agent.set_meta_context(agent_name="Agent1", phase="planning", iteration=1, round_num=1)

        result = asyncio.run(agent._multi_step_response_generation(system_prompt="s", user_prompt="u"))

        self.assertEqual(client.requests, [["user"], ["user", "model", "user"]])
        self.assertEqual(result["response"], "Done.")
        self.assertEqual(result["tools_executed"], 1)
        self.assertEqual(result["conversation_steps"], 2)


if __name__ == "__main__":
    unittest.main()
