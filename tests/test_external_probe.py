"""Probe failure handling and honest SLI accounting without external traffic."""

import io
import json
import unittest
from contextlib import contextmanager
from email.message import Message
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts.external_probe import check, report


class ProbeTests(unittest.TestCase):
    @contextmanager
    def response(self, body: bytes, status: int = 200) -> Any:
        response = io.BytesIO(body)
        response.status = status  # type: ignore[attr-defined]
        with patch("scripts.external_probe.build_opener") as factory:
            factory.return_value.open.return_value.__enter__.return_value = response
            yield factory.return_value.open

    def test_success_uses_read_and_encodes_id(self) -> None:
        with self.response(
            json.dumps({"id": "a/b", "title": "Probe"}).encode()
        ) as opened:
            result = check("https://example.test", "a/b", "Probe")
            request = opened.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, "https://example.test/issues/a%2Fb")
        self.assertTrue(result["success"])
        self.assertNotIn("Probe", json.dumps(result))

    def test_bad_bodies_are_failures(self) -> None:
        for body, reason in [
            (b"{}", "contract_mismatch"),
            (b"[]", "contract_mismatch"),
            (b"bad", "invalid_json"),
            (b"x" * 65537, "oversized_body"),
        ]:
            with self.subTest(reason=reason), self.response(body):
                result = check("https://example.test", "1", "Probe")
                self.assertFalse(result["success"])
                self.assertEqual(result["reason"], reason)

    def test_transport_and_http_failures(self) -> None:
        for error in [
            URLError("dns"),
            TimeoutError(),
            HTTPError("https://example.test", 404, "missing", Message(), None),
            HTTPError("https://example.test", 502, "provider", Message(), None),
        ]:
            with (
                self.subTest(error=error),
                patch("scripts.external_probe.build_opener") as factory,
            ):
                factory.return_value.open.side_effect = error
                result = check("https://example.test", "1", "Probe")
                self.assertFalse(result["success"])
                self.assertIsNotNone(result["duration_ms"])

    def test_redirect_is_not_followed(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        paths: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                paths.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/unexpected")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = check(f"http://127.0.0.1:{server.server_port}", "1", "Probe")
                self.assertFalse(result["success"])
                self.assertEqual(result["status"], 302)
                self.assertEqual(paths, ["/issues/1"])
            finally:
                server.shutdown()
                worker.join()

    def test_configuration_error_is_not_a_service_sample(self) -> None:
        for url in [
            "",
            "file:///tmp/test",
            "https://user:pass@example.test",
            "https://example.test?token=x",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                check(url, "1", "Probe")

    def test_report_counts_failures_deduplicates_and_rejects_mixed_targets(
        self,
    ) -> None:
        with self.response(b'{"id":"1","title":"Probe"}'):
            good = check("https://example.test", "1", "Probe")
        bad = dict(good, sample_id="bad", success=False, reason="network_error")
        self.assertIn("1/2 (50.00%)", report([good, good, bad]))
        self.assertIn("No data", report([]))
        with self.assertRaises(ValueError):
            report([good, dict(bad, target="another fixture")])
