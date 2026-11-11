"""Unit tests of the pure parsers without importing GTK or starting a display."""
import ast
import json
import math
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Extract only UI-independent functions to allow CI without system GTK packages.
MODULE = ast.parse((Path(__file__).resolve().parents[1] / "quota_pill.py").read_text(encoding="utf-8"))
PURE = {"make_empty_slot", "parse_pct_value", "parse_text_output", "infer_period_label", "_parse_codex_json", "format_reset_time"}
nodes = [n for n in MODULE.body if isinstance(n, ast.FunctionDef) and n.name in PURE]
namespace = {"json": json, "math": math, "re": re, "datetime": datetime, "timedelta": timedelta}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "quota_pill.py", "exec"), namespace)


def slots():
    empty = namespace["make_empty_slot"]
    return {"codex": empty(), "agy_g": empty(), "agy_c": empty()}


class ParserTests(unittest.TestCase):
    def test_percentage_decimal(self):
        parse = namespace["parse_pct_value"]
        self.assertEqual(parse("Session: 23.5% left"), 23.5)
        self.assertEqual(parse("Session: 99.9% left"), 99.9)
        self.assertEqual(parse("unknown"), None)

    def test_codex_json_remaining_not_used(self):
        res = slots()
        data = [{"provider": "codex", "usage": {
            "primary": {"usedPercent": 28, "windowMinutes": 300, "resetsAt": "2026-11-11T20:00:00Z"},
            "secondary": {"usedPercent": 4, "windowMinutes": 10080, "resetsAt": "2026-11-14T20:00:00Z"},
            "resetCredits": {"available": 2, "nextExpiresAt": "2026-12-01T03:50:00Z"}
        }}]
        namespace["_parse_codex_json"](json.dumps(data), res)
        c = res["codex"]
        self.assertTrue(c["ok"])
        self.assertEqual(c["p1"], 72)
        self.assertEqual(c["p2"], 96)
        self.assertEqual(c["credits"], "2 available")
        self.assertEqual(c["p1_label"], "5h")
        self.assertEqual(c["p2_label"], "周")

    def test_missing_window_stays_unknown(self):
        res = slots()
        namespace["_parse_codex_json"]('{"provider":"codex","usage":{"primary":{"usedPercent":100}}}', res)
        self.assertEqual(res["codex"]["p1"], 0)
        self.assertIsNone(res["codex"]["p2"])

    def test_antigravity_text(self):
        res = slots()
        text = """== Antigravity (api) ==
Gemini 5-hour: 100% left
Resets in 2h 10m
Claude/GPT 5-hour: 1% left
Resets in 45m
"""
        namespace["parse_text_output"](text, res)
        self.assertEqual(res["agy_g"]["p1"], 100)
        self.assertEqual(res["agy_c"]["p1"], 1)
        self.assertEqual(res["agy_c"]["p1_rst"], "45m")

    def test_reset_timestamp_and_duration(self):
        fmt = namespace["format_reset_time"]
        self.assertEqual(fmt("2d 21h", "abs_hm", base_ts=datetime(2026, 11, 11, 12).timestamp()), "09:00")
        self.assertRegex(fmt("2026-11-11T20:00:00Z", "abs_smart"), r"\d{2}:\d{2}|\d{2}-\d{2} \d{2}:\d{2}")
        self.assertEqual(fmt("--", "relative"), "--")


if __name__ == "__main__":
    unittest.main()
