"""LLM client against fake vLLM and llama.cpp servers (no network beyond 127.0.0.1, no database).

vLLM: exact model id or 404, reasoning in its own field, content starts with blank lines.
llama.cpp: model id mostly ignored, <think>...</think> inline in content, ```json fences,
older builds reject response_format json_schema.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import config
from services import llm

ANSWER = {"work_order": "5101-260715003", "planned_qty": 4000}
REPLY = "請按確認上傳。"
SCHEMA = {"type": "object", "properties": {"work_order": {"type": ["string", "null"]},
                                           "planned_qty": {"type": ["integer", "null"]}}}
MSGS = [{"role": "user", "content": "工單 5101-260715003，數量四千台"}]


class FakeServer(BaseHTTPRequestHandler):
    mode = "vllm"                     # vllm | llamacpp | llamacpp_old
    served = ["Qwen3.8-27B-FP8"]
    requests = []

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        owner = "vllm" if self.mode == "vllm" else "llamacpp"
        self._json(200, {"object": "list", "data": [{"id": m, "owned_by": owner} for m in self.served]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeServer.requests.append(req)
        if self.mode == "vllm" and req["model"] not in self.served:
            return self._json(404, {"error": {"message": f"The model `{req['model']}` does not exist."}})
        fmt = req.get("response_format") or {}
        if self.mode == "llamacpp_old" and fmt.get("type") == "json_schema":
            return self._json(400, {"error": {"message": "unsupported response_format"}})
        if self.mode == "vllm":
            content = "\n\n" + (json.dumps(ANSWER) if fmt else REPLY)
            message = {"role": "assistant", "content": content, "reasoning": "thinking..."}
        else:
            body = f"```json\n{json.dumps(ANSWER)}\n```" if fmt else REPLY
            content = "<think>\nlet me think\n</think>\n\n" + body
            message = {"role": "assistant", "content": content}
        if not req.get("stream"):
            return self._json(200, {"choices": [{"message": message}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if self.mode == "vllm":
            self.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant","reasoning":"x"}}]}\n\n')
        for i in range(0, len(content), 3):        # 3-char deltas split the <think> tags
            delta = {"choices": [{"delta": {"content": content[i:i + 3]}}]}
            self.wfile.write(f"data: {json.dumps(delta)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class BackendTests(unittest.TestCase):
    def setUp(self):
        FakeServer.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.patches = [patch.object(config, "LLM_ENABLED", True), patch.object(config, "LLM_API_BASE", base),
                        patch.object(llm, "_resolved_model", None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.server.shutdown()
        self.server.server_close()

    def use(self, mode, served, model):
        FakeServer.mode, FakeServer.served = mode, served
        p = patch.object(config, "LLM_MODEL", model)
        p.start()
        self.addCleanup(p.stop)

    def assert_all_calls(self, expected_model):
        self.assertEqual(llm.complete_json(MSGS, SCHEMA, temperature=0, max_tokens=128), ANSWER)
        self.assertEqual(llm.complete(MSGS), REPLY)
        self.assertEqual("".join(llm.stream_text(MSGS)), REPLY)
        status = llm.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["model"], expected_model)

    def test_vllm_configured_model(self):
        self.use("vllm", ["Qwen3.8-27B-FP8", "Qwen3.8-27B-UD-Q6_K_L"], "Qwen3.8-27B-FP8")
        self.assert_all_calls("Qwen3.8-27B-FP8")
        self.assertEqual(llm.status()["backend"], "vllm")

    def test_vllm_unknown_model_falls_back_to_served_one(self):
        self.use("vllm", ["Qwen3.8-27B-FP8"], "Qwen3.6-35B-A3B-Q6_K")
        self.assert_all_calls("Qwen3.8-27B-FP8")

    def test_auto_model(self):
        self.use("vllm", ["Qwen3.8-27B-FP8"], "auto")
        self.assert_all_calls("Qwen3.8-27B-FP8")

    def test_vllm_model_renamed_at_runtime(self):
        self.use("vllm", ["Old-Name"], "Old-Name")
        llm.probe()
        FakeServer.served = ["New-Name"]
        self.assertEqual(llm.complete(MSGS), REPLY)
        self.assertEqual(llm.active_model(), "New-Name")

    def test_llamacpp_inline_think_and_fenced_json(self):
        self.use("llamacpp", ["qwen3.8-27b-ud-q6_k_l.gguf"], "Qwen3.8-27B-FP8")
        self.assert_all_calls("qwen3.8-27b-ud-q6_k_l.gguf")

    def test_old_llamacpp_without_json_schema(self):
        self.use("llamacpp_old", ["model.gguf"], "auto")
        self.assert_all_calls("model.gguf")
        self.assertIn("json_object", [(r.get("response_format") or {}).get("type") for r in FakeServer.requests])

    def test_disabled(self):
        with patch.object(config, "LLM_ENABLED", False):
            llm.probe()
            self.assertFalse(llm.status()["enabled"])
            with self.assertRaises(llm.LlmDisabled):
                llm.complete(MSGS)
            with self.assertRaises(llm.LlmDisabled):
                list(llm.stream_text(MSGS))
        self.assertEqual(FakeServer.requests, [])


class ContentCleaningTests(unittest.TestCase):
    def test_clean_content(self):
        self.assertEqual(llm.clean_content(None), "")
        self.assertEqual(llm.clean_content("<think>a</think>\n\nOK"), "OK")
        self.assertEqual(llm.clean_content("<think>cut off by max_tokens"), "")
        self.assertEqual(llm.clean_content("reasoning</think>\nOK"), "OK")

    def test_parse_json_content(self):
        self.assertEqual(llm.parse_json_content('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json_content('Here: {"a": 1} done'), {"a": 1})
        with self.assertRaises(ValueError):
            llm.parse_json_content("no json")

    def test_stream_filter_handles_split_tags(self):
        f = llm._ThinkFilter()
        out = "".join(f.feed(p) for p in ["\n<th", "ink>xx</thi", "nk>\n\nHel", "lo <", "b>"]) + f.flush()
        self.assertEqual(out, "Hello <b>")


if __name__ == "__main__":
    unittest.main()
