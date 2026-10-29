import json
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from scripts import summarize_openai as summary


def response(data):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}


class SummarizerTests(unittest.TestCase):
    def setUp(self):
        self.paper = {"id": "http://arxiv.org/abs/2610.13200v1", "title": "HD 219134",
                      "summary": "A stellar study.", "authors": ["Jérôme Bétrisey"],
                      "categories": ["astro-ph.SR"]}

    def test_equivalent_arxiv_ids_keep_generated_summary(self):
        for returned in ("https://arxiv.org/abs/2610.13200v1", "2610.13200",
                         "https://arxiv.org/pdf/2610.13200v2.pdf"):
            with self.subTest(returned=returned), patch.object(
                    summary, "_call_api", return_value=response({"items": [
                        {"id": returned, "summary": "星震反演约束恒星性质。", "keywords": ["星震学"]}]})):
                items = summary._summarize_batch_items([self.paper], 1, "url", "model", "key", "")
            self.assertEqual(items[0]["id"], self.paper["id"])
            self.assertEqual(items[0]["summary"], "星震反演约束恒星性质。")

    def test_global_prompt_has_authors_and_normalizes_references(self):
        with patch.object(summary, "_call_api", return_value=response({
                "global_summary": "Jérôme Bétrisey 研究恒星性质。",
                "related_ids": ["https://arxiv.org/abs/2610.13200"],
                "groups": [{"label": "恒星", "indices": [1]}]})) as request:
            _, related, _ = summary._summarize_global_and_groups(
                [self.paper], [{"id": self.paper["id"], "summary": "恒星性质"}],
                "url", "model", "key", "用作者姓名指代")
        sent = json.loads(request.call_args.args[2]["messages"][1]["content"])
        self.assertEqual(sent["papers"][0]["authors"], ["Jérôme Bétrisey"])
        self.assertEqual(related, [self.paper["id"]])

    def test_incomplete_responses_are_rejected(self):
        truncated = response({"items": []})
        truncated["choices"][0]["finish_reason"] = "length"
        with self.assertRaisesRegex(ValueError, "truncated"):
            summary._response_json(truncated)
        with self.assertRaisesRegex(ValueError, "empty"):
            summary._response_json({"choices": [{"message": {"content": ""}}]})

    def test_deepseek_mode_is_provider_specific_and_preserves_explicit_choice(self):
        for url, payload, expected in (
            ("https://api.deepseek.com/chat/completions", {}, {"type": "disabled"}),
            ("https://api.deepseek.com/v1/chat/completions",
             {"thinking": {"type": "enabled"}}, {"type": "enabled"}),
            ("https://example.org/chat/completions", {}, None),
        ):
            completed = subprocess.CompletedProcess([], 0, json.dumps({"response": {"ok": True}}))
            with self.subTest(url=url, payload=payload), patch.object(
                    summary.subprocess, "run", return_value=completed) as worker:
                self.assertEqual(summary._call_api(url, "fake-key", payload), {"ok": True})
                sent = json.loads(worker.call_args.kwargs["input"])
                self.assertEqual(sent["payload"].get("thinking"), expected)
                self.assertNotIn("fake-key", " ".join(worker.call_args.args[0]))

    def test_openai_summarizes_both_phases_with_compatible_parameters(self):
        replies = [
            {"items": [{"id": self.paper["id"], "summary": "星震研究约束恒星性质。",
                        "keywords": ["星震学"]}]},
            {"global_summary": "Jérôme Bétrisey 研究恒星性质。",
             "related_ids": [self.paper["id"]],
             "groups": [{"label": "恒星", "indices": [1]}]},
        ]
        completed = [subprocess.CompletedProcess(
            [], 0, json.dumps({"response": response(reply)})) for reply in replies]
        with patch.object(summary.subprocess, "run", side_effect=completed) as worker:
            actual = summary.summarize_papers(
                [self.paper], "gpt-6-luna", "fake-key", "https://api.openai.com/v1")
        self.assertEqual(actual["items"][0]["summary"], replies[0]["items"][0]["summary"])
        self.assertEqual(actual["global_summary"], replies[1]["global_summary"])
        self.assertEqual(actual["groups"][0]["indices"], [1])
        self.assertEqual(worker.call_count, 2)
        for call in worker.call_args_list:
            sent = json.loads(call.kwargs["input"])
            self.assertEqual(sent["url"], "https://api.openai.com/v1/chat/completions")
            payload = sent["payload"]
            self.assertEqual(payload["model"], "gpt-6-luna")
            self.assertEqual(payload["max_completion_tokens"], 8192)
            self.assertEqual(payload["reasoning_effort"], "none")
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertNotIn("max_tokens", payload)
            self.assertNotIn("thinking", payload)

    def test_openai_parameters_preserve_overrides_and_input(self):
        payload = {"model": "gpt-6-luna", "max_tokens": 256,
                   "max_completion_tokens": 512, "reasoning_effort": "low"}
        original = dict(payload)
        completed = subprocess.CompletedProcess([], 0, json.dumps({"response": {"ok": True}}))
        with patch.object(summary.subprocess, "run", return_value=completed) as worker:
            summary._call_api("https://api.openai.com/v1/chat/completions", "fake-key", payload)
        sent = json.loads(worker.call_args.kwargs["input"])["payload"]
        self.assertEqual(sent["max_completion_tokens"], 512)
        self.assertEqual(sent["reasoning_effort"], "low")
        self.assertNotIn("max_tokens", sent)
        self.assertEqual(payload, original)

    def test_request_worker_reads_json_and_redacts_http_errors(self):
        class Response(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/error":
                    self.send_response(401)
                    body = b'{"error":"invalid fake-key"}'
                else:
                    self.send_response(200)
                    body = b'{"choices":[{"message":{"content":"ok"}}]}'
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Response)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            actual = summary._call_api(url, "fake-key", {})
            self.assertEqual(actual["choices"][0]["message"]["content"], "ok")
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                summary._call_api(url + "/error", "fake-key", {})
            self.assertNotIn("fake-key", str(raised.exception))
        finally:
            server.shutdown()
            server.server_close()

    def test_blank_keepalive_chunks_cannot_extend_request_deadline(self):
        class Keepalive(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b"1\r\n \r\n")
                        self.wfile.flush()
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Keepalive)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            with patch.object(summary, "REQUEST_TIMEOUT_SECONDS", 0.4):
                with self.assertRaisesRegex(TimeoutError, "in total"):
                    summary._call_api(f"http://127.0.0.1:{server.server_port}", "fake-key", {})
            self.assertLess(time.monotonic() - started, 2)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
