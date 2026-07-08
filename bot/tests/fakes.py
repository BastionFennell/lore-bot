"""Scripted fake Anthropic client for engine tests — no real API calls, ever."""

from __future__ import annotations


class FakeText:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeToolUse:
    def __init__(self, name: str, input: dict, id: str = "toolu_1"):
        self.type = "tool_use"
        self.name = name
        self.input = input
        self.id = id


class FakeMessage:
    def __init__(self, stop_reason: str, content: list):
        self.stop_reason = stop_reason
        self.content = content


class _FakeMessages:
    def __init__(self, parent: "FakeAnthropicClient"):
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        if not self._parent.responses:
            raise AssertionError("FakeAnthropicClient ran out of scripted responses")
        return self._parent.responses.pop(0)


class FakeAnthropicClient:
    def __init__(self, responses: list[FakeMessage]):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = _FakeMessages(self)
