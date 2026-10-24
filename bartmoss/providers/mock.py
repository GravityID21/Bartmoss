"""Offline provider with canned, format-correct replies. Free to run; used by tests and for
trying the CLI without an API key (`--model mock:echo`)."""

from __future__ import annotations

import re
from typing import Callable, Optional

from bartmoss.extract import top_module
from bartmoss.providers.base import ChatResult, Provider


def _last_user(messages: list[dict]) -> str:
    return next(m["content"] for m in reversed(messages) if m["role"] == "user")


def _first_file(messages: list[dict]) -> str:
    match = re.search(r"<file[^>]*>\n?(.*?)</file>", messages[0]["content"], re.S)
    return match.group(1) if match else ""


class MockProvider(Provider):
    name = "mock"

    def chat(
        self,
        system: str,
        messages: list[dict],
        *,
        model: str,
        max_tokens: int,
        effort: Optional[str] = None,
        on_text: Optional[Callable[[str], None]] = None,
        web_search: int = 0,
    ) -> ChatResult:
        if "MODE: filter" in system:
            question = _last_user(messages).lower()
            text = "OFF_TOPIC" if "recipe" in question else "HARMFUL" if "weapon" in question else "IN_SCOPE"
        elif "MODE: vplan" in system or "MODE: uvmplan" in system:
            from bartmoss.modes.plan import SECTIONS, TABLE_SECTIONS

            mode = "vplan" if "MODE: vplan" in system else "uvmplan"
            body = []
            for name in SECTIONS[mode]:
                table = "| a | b |\n|---|---|\n| mock | row |\n" if name in TABLE_SECTIONS[mode] else "Mock section.\n"
                body.append(f"# {name}\n{table}")
            text = "<plan>\n" + "\n".join(body) + "</plan>\n"
        elif "MODE: debug" in system:
            source = _first_file(messages)
            text = (
                "<analysis>\nSummary: mock review, no changes made.\nIssues:\n(none, mock provider)\n</analysis>\n"
                f"<fixed_file>\n{source}</fixed_file>\n"
            )
        elif "MODE: tb_vhdl" in system:
            top = top_module(_first_file(messages)) or "dut"
            text = (
                "<tb_file>\nentity tb_" + top + " is\nend entity;\n\narchitecture sim of tb_" + top + " is\nbegin\n"
                "  process\n  begin\n    report \"TEST PASSED\" severity note;\n    std.env.finish;\n  end process;\n"
                "end architecture;\n</tb_file>\n<notes>\n# Overview\nMock testbench with no real checks.\n</notes>\n"
            )
        elif "MODE: tb" in system:
            top = top_module(_first_file(messages)) or "dut"
            text = (
                "<tb_file>\n`timescale 1ns/1ps\n"
                f"module tb_{top};\n  initial begin\n    $display(\"TEST PASSED\");\n    $finish;\n  end\nendmodule\n"
                "</tb_file>\n<notes>\n# Overview\nMock testbench with no real checks.\n</notes>\n"
            )
        else:
            text = f"(mock) You asked: {_last_user(messages)[:200]}"
        if on_text:
            on_text(text)
        return ChatResult(text=text, model=f"mock:{model}", stop_reason="end_turn",
                          input_tokens=len(system + str(messages)) // 4, output_tokens=len(text) // 4)
