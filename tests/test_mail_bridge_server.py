import argparse
import json
from datetime import datetime
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mail_bridge_server import build_password_hash, make_server


API_TOKEN = "test-mail-token"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
USER_PASSWORD = "user-pass-123"


class MailBridgeServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config.json").write_text(
            json.dumps(
                {
                    "mail": {"api_key": API_TOKEN, "domain": "example.com"},
                    "auth": {
                        "session_secret": "test-session-secret",
                        "admin": {
                            "username": ADMIN_USERNAME,
                            "password_hash": build_password_hash(ADMIN_PASSWORD),
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.server = make_server(
            argparse.Namespace(
                host="127.0.0.1",
                port=0,
                db=str(self.root / "mail_bridge.sqlite3"),
                log_dir=str(self.root / "logs"),
                config=str(self.root / "config.json"),
                api_token=API_TOKEN,
                inbound_token=API_TOKEN,
            )
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.server.app.store.close()  # type: ignore[attr-defined]
        logger = self.server.app.logger  # type: ignore[attr-defined]
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        self.temp_dir.cleanup()

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        token: str = API_TOKEN,
        cookie: str = "",
        include_auth: bool = True,
        return_headers: bool = False,
    ) -> tuple[int, dict] | tuple[int, dict, dict]:
        data = None
        headers = {}
        if include_auth and token:
            headers["Authorization"] = f"Bearer {token}"
        if cookie:
            headers["Cookie"] = cookie
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
                if return_headers:
                    return response.status, body, dict(response.headers.items())
                return response.status, body
        except HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            code = exc.code
            headers = dict(exc.headers.items())
            exc.close()  # release the socket so GC can't raise a ResourceWarning mid-teardown
            if return_headers:
                return code, body, headers
            return code, body

    @staticmethod
    def _cookie_from_headers(headers: dict) -> str:
        set_cookie = str(headers.get("Set-Cookie") or "")
        return set_cookie.split(";", 1)[0].strip()

    def _post_inbound_mail(self, payload: dict) -> dict:
        status, body = self._request("POST", "/inbound/email", payload=payload)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        return body

    def _post_inbound_rfc822(self, raw_message: str, *, envelope_to: str = "", from_address: str = "") -> dict:
        headers = {
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": "message/rfc822",
        }
        if envelope_to:
            headers["x-mail-to"] = envelope_to
        if from_address:
            headers["x-mail-from"] = from_address
        request = Request(
            f"{self.base_url}/inbound/email",
            data=raw_message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        return body

    def _read_log_text(self) -> str:
        logger = self.server.app.logger  # type: ignore[attr-defined]
        for handler in logger.handlers:
            handler.flush()
        return (self.root / "logs" / "mail_bridge.log").read_text(encoding="utf-8")

    def _web_register(self, username: str, password: str) -> tuple[int, dict]:
        return self._request(
            "POST",
            "/web/auth/register",
            payload={"username": username, "password": password},
            include_auth=False,
        )  # type: ignore[return-value]

    def _web_login(self, username: str, password: str) -> tuple[int, dict, str]:
        status, body, headers = self._request(
            "POST",
            "/web/auth/login",
            payload={"username": username, "password": password},
            include_auth=False,
            return_headers=True,
        )  # type: ignore[assignment]
        cookie = self._cookie_from_headers(headers)
        return status, body, cookie

    def _create_mailbox_credential(self, address: str, admin_cookie: str) -> tuple[int, dict]:
        return self._request(
            "POST",
            "/web/admin/mailboxes",
            payload={"address": address},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[return-value]

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        token: str = API_TOKEN,
        cookie: str = "",
        include_auth: bool = True,
        host: str | None = None,
    ) -> tuple[int, bytes, dict]:
        headers = {}
        if include_auth and token:
            headers["Authorization"] = f"Bearer {token}"
        if cookie:
            headers["Cookie"] = cookie
        if host:
            headers["Host"] = host
        request = Request(f"{self.base_url}{path}", headers=headers, method=method)
        with urlopen(request, timeout=5) as response:
            return response.status, response.read(), dict(response.headers.items())

    def test_latest_returns_verification_code_for_address(self) -> None:
        self._post_inbound_mail(
            {
                "to": "Alice+001@Example.com",
                "from": "noreply@openai.com",
                "subject": "Your ChatGPT code is 654321",
                "text": "Use 654321 to continue.",
            }
        )

        status, body = self._request("GET", "/api/latest?address=alice+001@example.com")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["to"], "alice+001@example.com")
        self.assertEqual(body["email"]["verification_code"], "654321")
        self.assertEqual(body["email"]["mail_type"], "verification_code")

    def test_mail_times_default_to_beijing_time_in_inbound_and_latest_api(self) -> None:
        inbound = self._post_inbound_mail(
            {
                "to": "beijing@example.com",
                "from": "noreply@openai.com",
                "subject": "Your ChatGPT code is 777888",
                "text": "Use 777888 to continue.",
                "received_at": "2026-05-25T12:34:56Z",
            }
        )

        self.assertEqual(inbound["received_at"], "2026-05-25T20:34:56+08:00")

        status, body = self._request("GET", "/api/latest?address=beijing@example.com")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["received_at"], "2026-05-25T20:34:56+08:00")
        self.assertEqual(body["email"]["created_at"], "2026-05-25T20:34:56+08:00")

    def test_icloud_forwarded_mail_uses_original_recipient_address(self) -> None:
        self._post_inbound_rfc822(
            "\n".join(
                [
                    "From: relay@example.com",
                    "To: tureen_framing.4y<tureen_framing.4y@icloud.com>",
                    "Subject: Your ChatGPT code is 112233",
                    "Content-Type: text/plain; charset=utf-8",
                    "",
                    "Use 112233 to continue.",
                ]
            ),
            envelope_to="icloud@52moyu.net",
            from_address="relay@example.com",
        )

        status, body = self._request("GET", "/api/latest?address=tureen_framing.4y@icloud.com")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["to"], "tureen_framing.4y@icloud.com")
        self.assertEqual(body["email"]["verification_code"], "112233")

        status, alias_body = self._request("GET", "/api/latest?address=icloud@52moyu.net")
        self.assertEqual(status, 200)
        self.assertTrue(alias_body["ok"])
        self.assertIsNone(alias_body["email"])

    def test_icloud2_forwarded_mail_uses_original_recipient_address(self) -> None:
        self._post_inbound_rfc822(
            "\n".join(
                [
                    "From: relay@example.com",
                    "To: otro_veil.9q<otro_veil.9q@icloud.com>",
                    "Subject: Your ChatGPT code is 445566",
                    "Content-Type: text/plain; charset=utf-8",
                    "",
                    "Use 445566 to continue.",
                ]
            ),
            envelope_to="icloud2@52moyu.net",
            from_address="relay@example.com",
        )

        status, body = self._request("GET", "/api/latest?address=otro_veil.9q@icloud.com")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["to"], "otro_veil.9q@icloud.com")
        self.assertEqual(body["email"]["verification_code"], "445566")

        status, alias_body = self._request("GET", "/api/latest?address=icloud2@52moyu.net")
        self.assertEqual(status, 200)
        self.assertTrue(alias_body["ok"])
        self.assertIsNone(alias_body["email"])

    def test_icloud_forwarded_mail_can_restore_original_recipient_from_received_header(self) -> None:
        self._post_inbound_rfc822(
            "\n".join(
                [
                    "Received: from mail-oo2-f1.google.com with SMTP id 46e09a7af769-7e9b7237ccfso658488a34.1 for <amnesia.tap-8c@icloud.com>; Sun, 28 Jun 2026 08:22:33 -0700 (PDT)",
                    "From: relay@example.com",
                    "To: icloud@52moyu.net",
                    "Subject: Your ChatGPT code is 778899",
                    "Content-Type: text/plain; charset=utf-8",
                    "",
                    "Use 778899 to continue.",
                ]
            ),
            envelope_to="icloud@52moyu.net",
            from_address="relay@example.com",
        )

        status, body = self._request("GET", "/api/latest?address=amnesia.tap-8c@icloud.com")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["to"], "amnesia.tap-8c@icloud.com")
        self.assertEqual(body["email"]["verification_code"], "778899")

        status, alias_body = self._request("GET", "/api/latest?address=icloud@52moyu.net")
        self.assertEqual(status, 200)
        self.assertTrue(alias_body["ok"])
        self.assertIsNone(alias_body["email"])

    def test_invite_classification_and_link_extraction(self) -> None:
        self._post_inbound_mail(
            {
                "to": "invitee@example.com",
                "from": "team@openai.com",
                "subject": "Dennis Hill invited you to ChatGPT Business",
                "html": (
                    '<html><body><a href="https://chatgpt.com/invite/workspace/abc123">'
                    "Accept invitation</a></body></html>"
                ),
                "text": "Dennis Hill invited you to join workspace egg.",
            }
        )

        status, body = self._request("GET", "/api/invites/next?address=invitee@example.com")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["invite"]["to"], "invitee@example.com")
        self.assertEqual(body["invite"]["mail_type"], "team_invite")
        self.assertEqual(body["invite"]["invite_link"], "https://chatgpt.com/invite/workspace/abc123")
        self.assertEqual(body["invite"]["process_status"], "pending")

    def test_invite_with_six_digit_number_still_classified_as_invite(self) -> None:
        self._post_inbound_mail(
            {
                "to": "invitee+code@example.com",
                "from": "team@openai.com",
                "subject": "lu has invited you to ChatGPT Business",
                "html": (
                    '<html><body>'
                    '<p>Accept invitation before reference 205185 expires.</p>'
                    '<a href="https://chatgpt.com/invite/workspace/with-code">Accept invitation</a>'
                    "</body></html>"
                ),
                "text": "lu invited you to join workspace egg. Reference number: 205185.",
            }
        )

        status, body = self._request("GET", "/api/invites/next?address=invitee%2Bcode@example.com")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["invite"]["to"], "invitee+code@example.com")
        self.assertEqual(body["invite"]["mail_type"], "team_invite")
        self.assertEqual(body["invite"]["invite_link"], "https://chatgpt.com/invite/workspace/with-code")

    def test_invites_next_without_address_returns_oldest_pending_invite(self) -> None:
        self._post_inbound_mail(
            {
                "to": "older@example.com",
                "from": "team@openai.com",
                "subject": "First workspace invite",
                "text": "You were invited to join workspace alpha.",
                "html": '<a href="https://chatgpt.com/invite/workspace/oldest">Accept invitation</a>',
            }
        )
        self._post_inbound_mail(
            {
                "to": "newer@example.com",
                "from": "team@openai.com",
                "subject": "Second workspace invite",
                "text": "You were invited to join workspace beta.",
                "html": '<a href="https://chatgpt.com/invite/workspace/newer">Accept invitation</a>',
            }
        )

        status, body = self._request("GET", "/api/invites/next")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["invite"]["to"], "older@example.com")
        self.assertEqual(body["invite"]["invite_link"], "https://chatgpt.com/invite/workspace/oldest")

    def test_accepted_invite_is_not_returned_again(self) -> None:
        self._post_inbound_mail(
            {
                "to": "first@example.com",
                "from": "team@openai.com",
                "subject": "First invite",
                "text": "You were invited to join workspace alpha.",
                "html": '<a href="https://chatgpt.com/invite/workspace/first">Accept invitation</a>',
            }
        )
        self._post_inbound_mail(
            {
                "to": "second@example.com",
                "from": "team@openai.com",
                "subject": "Second invite",
                "text": "You were invited to join workspace beta.",
                "html": '<a href="https://chatgpt.com/invite/workspace/second">Accept invitation</a>',
            }
        )

        status, first_body = self._request("GET", "/api/invites/next")
        self.assertEqual(status, 200)
        first_invite = first_body["invite"]
        self.assertEqual(first_invite["to"], "first@example.com")

        status, mark_body = self._request(
            "POST",
            "/api/invites/mark",
            payload={"id": first_invite["id"], "status": "accepted", "note": "joined upstream"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(mark_body["ok"])
        self.assertEqual(mark_body["status"], "accepted")
        self.assertTrue(mark_body["processed_at"].endswith("+08:00"))
        datetime.fromisoformat(mark_body["processed_at"])

        status, second_body = self._request("GET", "/api/invites/next")
        self.assertEqual(status, 200)
        self.assertEqual(second_body["invite"]["to"], "second@example.com")
        self.assertEqual(second_body["invite"]["invite_link"], "https://chatgpt.com/invite/workspace/second")

    def test_inbound_logging_records_classification_and_persisted_fields(self) -> None:
        self._post_inbound_mail(
            {
                "to": "alice+001@example.com",
                "from": "noreply@openai.com",
                "subject": "Your ChatGPT code is 654321",
                "text": "Use 654321 to continue.",
            }
        )
        self._post_inbound_mail(
            {
                "to": "invitee@example.com",
                "from": "team@openai.com",
                "subject": "Dennis Hill invited you to ChatGPT Business",
                "html": (
                    '<html><body><a href="https://chatgpt.com/invite/workspace/abc123">'
                    "Accept invitation</a></body></html>"
                ),
                "text": "Dennis Hill invited you to join workspace egg.",
            }
        )

        log_text = self._read_log_text()

        self.assertIn(
            "classified inbound email: address=alice+001@example.com from=noreply@openai.com "
            "mail_type=verification_code has_code=yes has_invite_link=no",
            log_text,
        )
        self.assertIn(
            "stored inbound email: id=1 address=alice+001@example.com subject=Your ChatGPT code is 654321 "
            "mail_type=verification_code code=654321 invite_link=- process_status=pending",
            log_text,
        )
        self.assertIn(
            "classified inbound email: address=invitee@example.com from=team@openai.com "
            "mail_type=team_invite has_code=no has_invite_link=yes",
            log_text,
        )
        self.assertIn(
            "stored inbound email: id=2 address=invitee@example.com "
            "subject=Dennis Hill invited you to ChatGPT Business mail_type=team_invite "
            "code=- invite_link=https://chatgpt.com/invite/workspace/abc123 process_status=pending",
            log_text,
        )

    def test_inbound_logging_records_request_metadata_for_dedup_tracing(self) -> None:
        self._post_inbound_rfc822(
            "Message-ID: <abc123@example.com>\n"
            "To: Hide My Email <termini_rant.5o@icloud.com>\n"
            "From: OpenAI <noreply@openai.com>\n"
            "Subject: Your ChatGPT code is 112233\n"
            "\n"
            "Use 112233 to continue.",
            envelope_to="icloud@52moyu.net",
            from_address="noreply@openai.com",
        )

        log_text = self._read_log_text()

        self.assertIn(
            "inbound request: method=POST path=/inbound/email source_address=icloud@52moyu.net "
            "effective_address=termini_rant.5o@icloud.com to=icloud@52moyu.net "
            "from=noreply@openai.com message_id=<abc123@example.com>",
            log_text,
        )

    def test_web_login_returns_admin_dashboard_and_sets_session_cookie(self) -> None:
        status, body, cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["dashboard"], "/web/admin")
        self.assertIn("mail_bridge_session=", cookie)

    def test_web_login_returns_query_dashboard_for_normal_user(self) -> None:
        status, _ = self._web_register("normal-user", USER_PASSWORD)
        self.assertEqual(status, 200)
        status, body, cookie = self._web_login("normal-user", USER_PASSWORD)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["dashboard"], "/web/query")
        self.assertIn("mail_bridge_session=", cookie)

    def test_web_root_redirects_to_query_page(self) -> None:
        request = Request(f"{self.base_url}/web", headers={}, method="GET")
        with urlopen(request, timeout=5) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.geturl(), f"{self.base_url}/web/query")
        self.assertIn('/web/query-mails', html)

    def test_web_login_route_still_serves_public_query_page_for_compatibility(self) -> None:
        request = Request(f"{self.base_url}/web/login", headers={}, method="GET")
        with urlopen(request, timeout=5) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
        self.assertIn('/web/query-mails', html)
        self.assertIn('/web/query', html)

    def test_public_query_page_defaults_detail_modal_to_rendered_body_preview(self) -> None:
        request = Request(f"{self.base_url}/web/query", headers={}, method="GET")
        with urlopen(request, timeout=5) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
        self.assertIn('正文预览', html)
        self.assertIn('邮件源码', html)
        self.assertIn('setModalView("rendered");', html)

    def test_admin_page_embeds_auto_copy_logic_for_created_credentials(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, body, headers = self._raw_request("GET", "/web/admin", include_auth=False, cookie=admin_cookie)

        self.assertEqual(status, 200)
        self.assertIn("text/html", str(headers.get("Content-Type") or ""))
        html = body.decode("utf-8")
        self.assertIn("copyCreatedCredentials", html)
        self.assertIn("copied_credentials", html)
        self.assertIn("邮箱与密钥创建成功，凭据已复制到剪贴板", html)
        self.assertIn("新凭据已复制到剪贴板", html)
        self.assertIn("/web/admin/inbox", html)

    def test_admin_inbox_page_renders_list_shell_and_routes(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, body, headers = self._raw_request("GET", "/web/admin/inbox", include_auth=False, cookie=admin_cookie)

        self.assertEqual(status, 200)
        self.assertIn("text/html", str(headers.get("Content-Type") or ""))
        html = body.decode("utf-8")
        self.assertIn('id="inboxList"', html)
        self.assertIn('id="search-inbox"', html)
        self.assertIn("/web/admin/inbox/list", html)
        self.assertIn("openMailDetail", html)

    def test_admin_inbox_list_returns_recent_messages_with_pagination_across_addresses(self) -> None:
        self._post_inbound_mail(
            {
                "to": "first@example.com",
                "from": "sender@example.com",
                "subject": "First global",
                "text": "one",
            }
        )
        self._post_inbound_mail(
            {
                "to": "second@example.com",
                "from": "sender@example.com",
                "subject": "Second global",
                "text": "two",
            }
        )
        self._post_inbound_mail(
            {
                "to": "third@example.com",
                "from": "sender@example.com",
                "subject": "Third global",
                "text": "three",
            }
        )
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, body = self._request(
            "GET",
            "/web/admin/inbox/list?limit=2&offset=0",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)
        self.assertEqual([item["subject"] for item in body["emails"]], ["Third global", "Second global"])
        self.assertEqual([item["to"] for item in body["emails"]], ["third@example.com", "second@example.com"])

        status, page_two = self._request(
            "GET",
            "/web/admin/inbox/list?limit=2&offset=2",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(page_two["ok"])
        self.assertEqual(len(page_two["emails"]), 1)
        self.assertEqual(page_two["emails"][0]["subject"], "First global")

        status, filtered = self._request(
            "GET",
            "/web/admin/inbox/list?limit=10&offset=0&keyword=second",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(filtered["ok"])
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["keyword"], "second")
        self.assertEqual(len(filtered["emails"]), 1)
        self.assertEqual(filtered["emails"][0]["subject"], "Second global")

        status, filtered_address = self._request(
            "GET",
            "/web/admin/inbox/list?limit=10&offset=0&keyword=third@example.com",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(filtered_address["ok"])
        self.assertEqual(filtered_address["total"], 1)
        self.assertEqual(filtered_address["emails"][0]["to"], "third@example.com")

    def test_admin_inbox_detail_returns_message_by_id(self) -> None:
        self._post_inbound_mail(
            {
                "to": "detail-global@example.com",
                "from": "detail-sender@example.com",
                "subject": "Global detail",
                "html": "<html><body><p>Inbox detail</p></body></html>",
                "text": "Inbox detail text",
            }
        )
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, inbox_body = self._request(
            "GET",
            "/web/admin/inbox/list?limit=1&offset=0",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        message_id = int(inbox_body["emails"][0]["id"])

        status, body = self._request(
            "GET",
            f"/web/admin/inbox/{message_id}",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["subject"], "Global detail")
        self.assertEqual(body["email"]["to"], "detail-global@example.com")
        self.assertEqual(body["email"]["html"], "<html><body><p>Inbox detail</p></body></html>")

    def test_admin_inbox_page_and_api_require_admin_session(self) -> None:
        request = Request(f"{self.base_url}/web/admin/inbox", headers={}, method="GET")
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.geturl(), f"{self.base_url}/web/admin/login")

        status, _ = self._web_register("normal-inbox-user", USER_PASSWORD)
        self.assertEqual(status, 200)
        status, _, user_cookie = self._web_login("normal-inbox-user", USER_PASSWORD)
        self.assertEqual(status, 200)

        user_page_request = Request(
            f"{self.base_url}/web/admin/inbox",
            headers={"Cookie": user_cookie},
            method="GET",
        )
        with urlopen(user_page_request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.geturl(), f"{self.base_url}/web/admin/login")

        user_api_request = Request(
            f"{self.base_url}/web/admin/inbox/list?limit=1",
            headers={"Cookie": user_cookie},
            method="GET",
        )
        with urlopen(user_api_request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.geturl(), f"{self.base_url}/web/admin/login")

    def test_public_query_returns_mail_list_for_valid_mailbox_credential(self) -> None:
        self._post_inbound_mail(
            {
                "to": "alice+001@example.com",
                "from": "noreply@openai.com",
                "subject": "Your ChatGPT code is 222333",
                "text": "Use 222333 to continue.",
            }
        )
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, body = self._create_mailbox_credential("Alice+001@Example.com", admin_cookie)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        credential = body["credential"]
        status, body = self._request(
            "POST",
            "/web/query-mails",
            payload={"credential": credential},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["mailbox"]["address"], "alice+001@example.com")
        self.assertEqual(body["emails"][0]["verification_code"], "222333")
        self.assertEqual(body["emails"][0]["to"], "alice+001@example.com")

    def test_public_query_detail_requires_valid_mailbox_key(self) -> None:
        self._post_inbound_mail(
            {
                "to": "detail@example.com",
                "from": "sender@example.com",
                "subject": "HTML test",
                "html": "<html><body><h1>Hello</h1></body></html>",
                "text": "Hello",
            }
        )
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, created = self._create_mailbox_credential("detail@example.com", admin_cookie)
        self.assertEqual(status, 200)
        credential = created["credential"]
        status, query = self._request(
            "POST",
            "/web/query-mails",
            payload={"credential": credential},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        mail_id = int(query["emails"][0]["id"])
        status, body = self._request(
            "POST",
            "/web/query-mail-detail",
            payload={"address": "detail@example.com", "key": "wrong-key", "id": mail_id},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "invalid_credential")

        status, body = self._request(
            "POST",
            "/web/query-mail-detail",
            payload={"address": "detail@example.com", "key": credential.split("----", 1)[1], "id": mail_id},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["html"], "<html><body><h1>Hello</h1></body></html>")

    def test_rfc822_raw_mail_is_preserved_for_detail_view(self) -> None:
        raw_message = "\r\n".join(
            [
                "From: sender@example.com",
                "To: detail-raw@example.com",
                "Subject: Raw Detail",
                "X-Custom-Trace: keep-order",
                "Content-Type: text/plain; charset=utf-8",
                "",
                "",
                "Line 1",
                "Line 2",
                "",
            ]
        )
        self._post_inbound_rfc822(raw_message, envelope_to="detail-raw@example.com", from_address="sender@example.com")

        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, created = self._create_mailbox_credential("detail-raw@example.com", admin_cookie)
        self.assertEqual(status, 200)
        credential = created["credential"]
        status, query = self._request(
            "POST",
            "/web/query-mails",
            payload={"credential": credential},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        mail_id = int(query["emails"][0]["id"])

        status, body = self._request(
            "POST",
            "/web/query-mail-detail",
            payload={"address": "detail-raw@example.com", "key": credential.split("----", 1)[1], "id": mail_id},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["email"]["raw_mail"], raw_message)
        self.assertEqual(
            body["email"]["raw_header_text"],
            "\r\n".join(
                [
                    "From: sender@example.com",
                    "To: detail-raw@example.com",
                    "Subject: Raw Detail",
                    "X-Custom-Trace: keep-order",
                    "Content-Type: text/plain; charset=utf-8",
                ]
            ),
        )

    def test_rfc822_encoded_subject_is_decoded_for_inbox_and_detail(self) -> None:
        encoded_subject = "=?UTF-8?B?5L2g55qE5Li05pe2IE9wZW5BSSDnmbvlvZXku6PnoIE=?="
        raw_message = "\r\n".join(
            [
                "From: sender@example.com",
                "To: encoded-subject@example.com",
                f"Subject: {encoded_subject}",
                "Content-Type: text/plain; charset=utf-8",
                "",
                "",
                "body",
            ]
        )
        self._post_inbound_rfc822(raw_message, envelope_to="encoded-subject@example.com", from_address="sender@example.com")

        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, inbox = self._request(
            "GET",
            "/web/admin/inbox/list?limit=10&offset=0&keyword=encoded-subject@example.com",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(inbox["ok"])
        self.assertEqual(inbox["emails"][0]["subject"], "你的临时 OpenAI 登录代码")

        message_id = int(inbox["emails"][0]["id"])
        status, detail = self._request(
            "GET",
            f"/web/admin/inbox/{message_id}",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["email"]["subject"], "你的临时 OpenAI 登录代码")

    def test_json_inbound_accepts_explicit_raw_mail_fields(self) -> None:
        raw_mail = "Header-A: first\nHeader-B: second\n\n  preserved body  \n"
        raw_header_text = "Header-A: first\nHeader-B: second"
        self._post_inbound_mail(
            {
                "to": "json-raw@example.com",
                "from": "sender@example.com",
                "subject": "JSON Raw Mail",
                "text": "structured text",
                "body": "trimmed body",
                "raw_mail": raw_mail,
                "raw_header_text": raw_header_text,
            }
        )

        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, created = self._create_mailbox_credential("json-raw@example.com", admin_cookie)
        self.assertEqual(status, 200)
        credential = created["credential"]
        status, query = self._request(
            "POST",
            "/web/query-mails",
            payload={"credential": credential},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        mail_id = int(query["emails"][0]["id"])

        status, body = self._request(
            "POST",
            "/web/query-mail-detail",
            payload={"address": "json-raw@example.com", "key": credential.split("----", 1)[1], "id": mail_id},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(body["email"]["raw_mail"], raw_mail)
        self.assertEqual(body["email"]["raw_header_text"], raw_header_text)
        self.assertEqual(body["email"]["body"], "trimmed body")

    def test_admin_can_list_reset_and_toggle_mailbox_credential(self) -> None:
        self._post_inbound_mail(
            {
                "to": "ops@example.com",
                "from": "noreply@openai.com",
                "subject": "Your ChatGPT code is 445566",
                "text": "Use 445566 to continue.",
            }
        )
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, create_body = self._create_mailbox_credential("ops@example.com", admin_cookie)
        self.assertEqual(status, 200)
        old_credential = create_body["credential"]
        old_key = create_body["access_key"]
        mailbox_id = int(create_body["mailbox"]["id"])

        status, list_body = self._request("GET", "/web/admin/mailboxes", include_auth=False, cookie=admin_cookie)  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(list_body["ok"])
        self.assertEqual(list_body["total"], 1)
        self.assertEqual(list_body["limit"], 20)
        self.assertEqual(list_body["offset"], 0)
        self.assertEqual(list_body["mailboxes"][0]["address"], "ops@example.com")
        self.assertTrue(list_body["mailboxes"][0]["created_at"])
        self.assertEqual(list_body["mailboxes"][0]["note"], "")

        status, reset_body = self._request(
            "POST",
            f"/web/admin/mailboxes/{mailbox_id}/reset-key",
            payload={},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(reset_body["ok"])
        self.assertNotEqual(old_key, reset_body["access_key"])
        self.assertTrue(reset_body["mailbox"]["created_at"])
        self.assertEqual(reset_body["mailbox"]["note"], "")

        status, body = self._request(
            "POST",
            "/web/query-mails",
            payload={"credential": old_credential},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "invalid_credential")

        status, body = self._request(
            "POST",
            f"/web/admin/mailboxes/{mailbox_id}/toggle-active",
            payload={"active": False},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertFalse(body["mailbox"]["active"])

        new_credential = reset_body["credential"]
        status, body = self._request(
            "POST",
            "/web/query-mails",
            payload={"credential": new_credential},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "mailbox_inactive")

    def test_admin_can_create_assign_filter_and_delete_mailbox_tags(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, vip_tag = self._request(
            "POST",
            "/web/admin/tags",
            payload={"name": "VIP"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(vip_tag["ok"])
        vip_id = int(vip_tag["tag"]["id"])

        status, retry_tag = self._request(
            "POST",
            "/web/admin/tags",
            payload={"name": "VIP"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 409)
        self.assertEqual(retry_tag["error"], "tag_exists")

        status, ops_tag = self._request(
            "POST",
            "/web/admin/tags",
            payload={"name": "运营"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        ops_id = int(ops_tag["tag"]["id"])

        status, body_a = self._create_mailbox_credential("tag-a@example.com", admin_cookie)
        self.assertEqual(status, 200)
        mailbox_a_id = int(body_a["mailbox"]["id"])
        status, body_b = self._create_mailbox_credential("tag-b@example.com", admin_cookie)
        self.assertEqual(status, 200)
        mailbox_b_id = int(body_b["mailbox"]["id"])

        status, set_a = self._request(
            "POST",
            f"/web/admin/mailboxes/{mailbox_a_id}/tags",
            payload={"tag_ids": [vip_id, ops_id]},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual([tag["name"] for tag in set_a["mailbox"]["tags"]], ["VIP", "运营"])

        status, set_b = self._request(
            "POST",
            f"/web/admin/mailboxes/{mailbox_b_id}/tags",
            payload={"tag_ids": [ops_id]},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual([tag["name"] for tag in set_b["mailbox"]["tags"]], ["运营"])

        status, tag_list = self._request(
            "GET",
            "/web/admin/tags",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(tag_list["tags"][0]["name"], "VIP")
        by_name = {item["name"]: item for item in tag_list["tags"]}
        self.assertEqual(by_name["VIP"]["mailbox_count"], 1)
        self.assertEqual(by_name["运营"]["mailbox_count"], 2)

        status, filtered = self._request(
            "GET",
            f"/web/admin/mailboxes?tag_id={vip_id}",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["mailboxes"][0]["address"], "tag-a@example.com")
        self.assertEqual([tag["name"] for tag in filtered["mailboxes"][0]["tags"]], ["VIP", "运营"])

        status, deleted = self._request(
            "POST",
            f"/web/admin/tags/{vip_id}/delete",
            payload={},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])

        status, after_delete = self._request(
            "GET",
            "/web/admin/mailboxes",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        mailbox_a = next(item for item in after_delete["mailboxes"] if item["address"] == "tag-a@example.com")
        self.assertEqual([tag["name"] for tag in mailbox_a["tags"]], ["运营"])

    def test_new_mailboxes_default_to_empty_tags(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, created = self._create_mailbox_credential("default-tag@example.com", admin_cookie)
        self.assertEqual(status, 200)

        status, list_body = self._request(
            "GET",
            "/web/admin/mailboxes",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        mailbox = next(item for item in list_body["mailboxes"] if item["address"] == "default-tag@example.com")
        self.assertEqual(mailbox["tags"], [])

    def test_admin_mailboxes_list_supports_pagination_and_keyword_filter(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        for idx in range(1, 121):
            status, _ = self._create_mailbox_credential(f"bulk{idx:03d}@example.com", admin_cookie)
            self.assertEqual(status, 200)

        status, default_page = self._request(
            "GET",
            "/web/admin/mailboxes",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(default_page["ok"])
        self.assertEqual(default_page["total"], 120)
        self.assertEqual(default_page["limit"], 20)
        self.assertEqual(default_page["offset"], 0)
        self.assertEqual(len(default_page["mailboxes"]), 20)
        self.assertEqual(default_page["mailboxes"][0]["address"], "bulk001@example.com")
        self.assertEqual(default_page["mailboxes"][-1]["address"], "bulk020@example.com")

        status, page1 = self._request(
            "GET",
            "/web/admin/mailboxes?limit=50&offset=0",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(page1["ok"])
        self.assertEqual(page1["total"], 120)
        self.assertEqual(page1["limit"], 50)
        self.assertEqual(page1["offset"], 0)
        self.assertEqual(len(page1["mailboxes"]), 50)
        self.assertEqual(page1["mailboxes"][0]["address"], "bulk001@example.com")
        self.assertEqual(page1["mailboxes"][-1]["address"], "bulk050@example.com")

        status, page3 = self._request(
            "GET",
            "/web/admin/mailboxes?limit=50&offset=100",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(page3["total"], 120)
        self.assertEqual(page3["offset"], 100)
        self.assertEqual(len(page3["mailboxes"]), 20)
        self.assertEqual(page3["mailboxes"][0]["address"], "bulk101@example.com")
        self.assertEqual(page3["mailboxes"][-1]["address"], "bulk120@example.com")

        status, filtered = self._request(
            "GET",
            "/web/admin/mailboxes?limit=50&offset=0&keyword=bulk11",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(filtered["total"], 10)
        self.assertEqual(len(filtered["mailboxes"]), 10)
        self.assertEqual(filtered["mailboxes"][0]["address"], "bulk110@example.com")
        self.assertEqual(filtered["mailboxes"][-1]["address"], "bulk119@example.com")

    def test_admin_can_create_mailbox_with_note_update_note_and_export_csv(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, create_body = self._request(
            "POST",
            "/web/admin/mailboxes",
            payload={"address": "note@example.com", "note": "首发备注"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(create_body["ok"])
        mailbox_id = int(create_body["mailbox"]["id"])
        self.assertEqual(create_body["mailbox"]["note"], "首发备注")
        self.assertTrue(create_body["mailbox"]["created_at"])

        status, list_body = self._request(
            "GET",
            "/web/admin/mailboxes?keyword=%E9%A6%96%E5%8F%91",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(list_body["total"], 1)
        self.assertEqual(list_body["mailboxes"][0]["note"], "首发备注")
        self.assertTrue(list_body["mailboxes"][0]["access_key"])

        status, note_body = self._request(
            "POST",
            f"/web/admin/mailboxes/{mailbox_id}/note",
            payload={"note": "更新备注"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(note_body["ok"])
        self.assertEqual(note_body["mailbox"]["note"], "更新备注")

        status, raw_body, headers = self._raw_request(
            "GET",
            "/web/admin/mailboxes/export.csv",
            include_auth=False,
            cookie=admin_cookie,
        )
        self.assertEqual(status, 200)
        self.assertIn("text/csv", str(headers.get("Content-Type") or ""))
        self.assertIn('attachment; filename="mailboxes-export.csv"', str(headers.get("Content-Disposition") or ""))
        csv_text = raw_body.decode("utf-8-sig")
        self.assertEqual(csv_text.strip(), f"note@example.com----{create_body['access_key']}")

    def test_admin_can_bulk_import_mailboxes_from_multiline_text(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, body = self._request(
            "POST",
            "/web/admin/mailboxes/import-bulk",
            payload={
                "content": "\n".join(
                    [
                        "bulk-one@example.com",
                        "",
                        "not-an-email",
                        "bulk-two@example.com",
                        "bulk-one@example.com",
                    ]
                ),
                "note": "批量导入",
            },
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        summary = body["summary"]
        self.assertEqual(summary["accepted_lines"], 4)
        self.assertEqual(summary["created"], 2)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["invalid"], 1)

        results = summary["results"]
        created_rows = [item for item in results if item["status"] == "created"]
        invalid_rows = [item for item in results if item["status"] == "invalid"]
        skipped_rows = [item for item in results if item["status"] == "skipped"]
        self.assertEqual(len(created_rows), 2)
        self.assertEqual(created_rows[0]["mailbox"]["note"], "批量导入")
        self.assertIn("----", created_rows[0]["mailbox"]["credential"])
        self.assertEqual(invalid_rows[0]["reason"], "invalid_address")
        self.assertEqual(skipped_rows[0]["reason"], "duplicate_in_request")

        status, list_body = self._request(
            "GET",
            "/web/admin/mailboxes?keyword=%E6%89%B9%E9%87%8F%E5%AF%BC%E5%85%A5",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(list_body["total"], 2)
        self.assertEqual(list_body["mailboxes"][0]["note"], "批量导入")

    def test_admin_can_import_csv_with_create_and_overwrite_behavior(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, existing = self._request(
            "POST",
            "/web/admin/mailboxes",
            payload={"address": "csv-existing@example.com", "note": "旧备注"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        old_key = existing["access_key"]

        status, body = self._request(
            "POST",
            "/web/admin/mailboxes/import-csv",
            payload={
                "content": "\n".join(
                    [
                        f"csv-existing@example.com----NEWKEY123456",
                        "csv-new@example.com----CSVNEW654321",
                        "csv-generated@example.com",
                    ]
                ),
                "note": "CSV导入",
            },
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        summary = body["summary"]
        self.assertEqual(summary["created"], 2)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["invalid"], 0)

        updated_row = next(item for item in summary["results"] if item["status"] == "updated")
        self.assertEqual(updated_row["address"], "csv-existing@example.com")
        self.assertEqual(updated_row["mailbox"]["access_key"], "NEWKEY123456")
        self.assertNotEqual(old_key, updated_row["mailbox"]["access_key"])

        created_given = next(item for item in summary["results"] if item["address"] == "csv-new@example.com")
        self.assertEqual(created_given["mailbox"]["access_key"], "CSVNEW654321")
        created_generated = next(item for item in summary["results"] if item["address"] == "csv-generated@example.com")
        self.assertTrue(created_generated["mailbox"]["access_key"])
        self.assertEqual(created_generated["mailbox"]["note"], "CSV导入")

        status, list_body = self._request(
            "GET",
            "/web/admin/mailboxes?keyword=csv-",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        by_address = {item["address"]: item for item in list_body["mailboxes"]}
        self.assertEqual(by_address["csv-existing@example.com"]["access_key"], "NEWKEY123456")
        self.assertEqual(by_address["csv-existing@example.com"]["note"], "CSV导入")
        self.assertEqual(by_address["csv-new@example.com"]["access_key"], "CSVNEW654321")
        self.assertEqual(by_address["csv-generated@example.com"]["note"], "CSV导入")

    def test_admin_can_import_csv_with_tags(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)

        status, tag_body = self._request(
            "POST",
            "/web/admin/tags",
            payload={"name": "CSV标签"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        tag_id = tag_body["tag"]["id"]

        status, body = self._request(
            "POST",
            "/web/admin/mailboxes/import-csv",
            payload={
                "content": "csv-tagged@example.com----CSVTAG123456",
                "note": "CSV标签导入",
                "tag_ids": [tag_id],
            },
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

        status, list_body = self._request(
            "GET",
            "/web/admin/mailboxes?keyword=csv-tagged@example.com",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(list_body["total"], 1)
        mailbox = list_body["mailboxes"][0]
        self.assertEqual(mailbox["address"], "csv-tagged@example.com")
        self.assertEqual(mailbox["status"], "presale")
        self.assertTrue(any(tag["id"] == tag_id for tag in mailbox["tags"]))
        self.assertEqual(mailbox["tags"][0]["name"], "CSV标签")

    def test_admin_mailbox_email_list_endpoint_returns_recent_messages(self) -> None:
        self._post_inbound_mail(
            {
                "to": "recent@example.com",
                "from": "news@example.com",
                "subject": "First",
                "text": "Body one",
            }
        )
        self._post_inbound_mail(
            {
                "to": "recent@example.com",
                "from": "news@example.com",
                "subject": "Second",
                "text": "Body two",
            }
        )
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, create_body = self._create_mailbox_credential("recent@example.com", admin_cookie)
        self.assertEqual(status, 200)
        mailbox_id = int(create_body["mailbox"]["id"])
        status, body = self._request(
            "GET",
            f"/web/admin/mailboxes/{mailbox_id}/emails",
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["mailbox"]["address"], "recent@example.com")
        self.assertEqual(len(body["emails"]), 2)
        self.assertEqual(body["emails"][0]["subject"], "Second")

    def test_query_endpoints_do_not_expose_admin_only_tags(self) -> None:
        status, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        status, tag_body = self._request(
            "POST",
            "/web/admin/tags",
            payload={"name": "隐藏标签"},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        tag_id = int(tag_body["tag"]["id"])

        self._post_inbound_mail(
            {
                "to": "hidden-tag@example.com",
                "from": "noreply@openai.com",
                "subject": "Your ChatGPT code is 987654",
                "text": "Use 987654 to continue.",
            }
        )
        status, create_body = self._create_mailbox_credential("hidden-tag@example.com", admin_cookie)
        self.assertEqual(status, 200)
        mailbox_id = int(create_body["mailbox"]["id"])
        credential = create_body["credential"]

        status, set_tags = self._request(
            "POST",
            f"/web/admin/mailboxes/{mailbox_id}/tags",
            payload={"tag_ids": [tag_id]},
            include_auth=False,
            cookie=admin_cookie,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(set_tags["ok"])

        status, query_body = self._request(
            "POST",
            "/web/query-mails",
            payload={"credential": credential},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(query_body["ok"])
        self.assertNotIn("tags", query_body["mailbox"])
        self.assertNotIn("tags", query_body["emails"][0])

        mail_id = int(query_body["emails"][0]["id"])
        status, detail_body = self._request(
            "POST",
            "/web/query-mail-detail",
            payload={"address": "hidden-tag@example.com", "key": credential.split("----", 1)[1], "id": mail_id},
            include_auth=False,
        )  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertTrue(detail_body["ok"])
        self.assertNotIn("tags", detail_body["email"])


    # ---- CDK email-selling platform guardrail (inventory, anonymous +
    # account redemption, stock accounting, concurrency) ----

    def _admin_cookie(self) -> str:
        status, _, cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        return cookie

    def _create_tag(self, name: str, cookie: str) -> int:
        status, body = self._request(
            "POST", "/web/admin/tags", payload={"name": name}, include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 200, body)
        return int(body["tag"]["id"])

    def _bulk_import(self, content: str, cookie: str, tag_ids=None) -> dict:
        payload: dict = {"content": content}
        if tag_ids:
            payload["tag_ids"] = tag_ids
        status, body = self._request(
            "POST", "/web/admin/mailboxes/import-bulk", payload=payload, include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 200, body)
        return body

    def _gen_cdk(self, cookie: str, *, tag_id: int = 0, quantity: int = 1, count: int = 1) -> list:
        status, body = self._request(
            "POST",
            "/web/admin/cdks",
            payload={"count": count, "tag_id": tag_id, "quantity": quantity},
            include_auth=False,
            cookie=cookie,
        )
        self.assertEqual(status, 200, body)
        return body["codes"]

    def _stock(self, cookie: str) -> dict:
        status, body = self._request("GET", "/web/admin/stock", include_auth=False, cookie=cookie)
        self.assertEqual(status, 200, body)
        return body["stock"]

    def _redeem(self, code: str, *, cookie: str = "") -> tuple[int, dict]:
        # No bearer auth: web redeem authenticates only via the session cookie,
        # so an empty cookie genuinely exercises the anonymous path.
        return self._request(
            "POST", "/web/user/redeem", payload={"code": code}, include_auth=False, cookie=cookie
        )  # type: ignore[return-value]

    def _tag_stock(self, stock: dict, tag_id: int) -> dict:
        for row in stock["tags"]:
            if int(row["id"]) == tag_id:
                return row
        self.fail(f"tag {tag_id} not in stock summary {stock}")

    def test_anonymous_redeem_dispenses_credential_and_decrements_stock(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com\nb@x.com", cookie, tag_ids=[tag_id])
        # Imported stock lands in the presale pool until a CDK reserves it.
        before = self._tag_stock(self._stock(cookie), tag_id)
        self.assertEqual(before["presale"], 2)
        self.assertEqual(before["available"], 0)
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]
        # Generating the CDK moves one mailbox from presale into the redeemable pool.
        after_gen = self._tag_stock(self._stock(cookie), tag_id)
        self.assertEqual(after_gen["presale"], 1)
        self.assertEqual(after_gen["available"], 1)

        status, body = self._redeem(code)  # anonymous: no cookie
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["mailboxes"]), 1)
        mailbox = body["mailboxes"][0]
        self.assertIn("----", mailbox["credential"])
        self.assertTrue(mailbox["access_key"])

        after = self._tag_stock(self._stock(cookie), tag_id)
        self.assertEqual(after["available"], 0)
        self.assertEqual(after["sold"], 1)

    def test_anonymous_redeem_again_returns_same_mailbox(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com\nb@x.com", cookie, tag_ids=[tag_id])
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]

        status1, body1 = self._redeem(code)
        self.assertEqual(status1, 200, body1)
        self.assertFalse(body1.get("reused"))
        addr1 = body1["mailboxes"][0]["address"]

        # Re-redeeming the same code returns the SAME mailbox — recovery for a
        # buyer who cleared their cache — never a second account, never extra stock.
        status2, body2 = self._redeem(code)
        self.assertEqual(status2, 200, body2)
        self.assertTrue(body2.get("reused"))
        self.assertEqual(body2["mailboxes"][0]["address"], addr1)
        # Stock only moved by the one real dispense.
        self.assertEqual(self._tag_stock(self._stock(cookie), tag_id)["sold"], 1)

    def test_reredeem_claims_anonymous_mailbox_into_account(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]

        # First redeemed anonymously (not bound to any account).
        status, body = self._redeem(code)
        self.assertEqual(status, 200, body)
        address = body["mailboxes"][0]["address"]

        # A buyer who cleared their cache signs up and re-redeems the SAME code:
        # same mailbox, now claimed into their account.
        self.assertEqual(self._web_register("buyer", USER_PASSWORD)[0], 200)
        _, _, user_cookie = self._web_login("buyer", USER_PASSWORD)
        status, body = self._redeem(code, cookie=user_cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("reused"))
        self.assertEqual(body["mailboxes"][0]["address"], address)

        status, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=user_cookie)
        self.assertEqual(status, 200, mine)
        self.assertIn(address, [m["address"] for m in mine["mailboxes"]])

    def test_user_delete_mailbox_unlinks_and_is_recoverable(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com\nb@x.com", cookie, tag_ids=[tag_id])
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]

        self.assertEqual(self._web_register("buyer", USER_PASSWORD)[0], 200)
        _, _, user_cookie = self._web_login("buyer", USER_PASSWORD)

        status, body = self._redeem(code, cookie=user_cookie)
        self.assertEqual(status, 200, body)
        address = body["mailboxes"][0]["address"]
        _, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=user_cookie)
        self.assertIn(address, [m["address"] for m in mine["mailboxes"]])

        # Delete = unlink from my list.
        status, body = self._request(
            "POST", "/web/me/mailboxes/delete", payload={"address": address}, include_auth=False, cookie=user_cookie
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["deleted"], 1)
        _, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=user_cookie)
        self.assertNotIn(address, [m["address"] for m in mine["mailboxes"]])

        # Recoverable: re-redeeming the same code re-claims the same mailbox.
        status, body = self._redeem(code, cookie=user_cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("reused"))
        self.assertEqual(body["mailboxes"][0]["address"], address)
        _, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=user_cookie)
        self.assertIn(address, [m["address"] for m in mine["mailboxes"]])

    def test_user_delete_skips_mailbox_not_owned(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com\nb@x.com", cookie, tag_ids=[tag_id])
        code_a = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]
        code_b = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]

        self.assertEqual(self._web_register("buyer1", USER_PASSWORD)[0], 200)
        _, _, c1 = self._web_login("buyer1", USER_PASSWORD)
        addr1 = self._redeem(code_a, cookie=c1)[1]["mailboxes"][0]["address"]

        self.assertEqual(self._web_register("buyer2", USER_PASSWORD)[0], 200)
        _, _, c2 = self._web_login("buyer2", USER_PASSWORD)
        self._redeem(code_b, cookie=c2)

        # buyer2 cannot delete buyer1's mailbox — it is skipped, not removed.
        status, body = self._request(
            "POST", "/web/me/mailboxes/delete", payload={"addresses": [addr1]}, include_auth=False, cookie=c2
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["deleted"], 0)
        self.assertIn(addr1, body["skipped"])
        _, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=c1)
        self.assertIn(addr1, [m["address"] for m in mine["mailboxes"]])

    def test_insufficient_stock_preserves_inventory(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com\nb@x.com", cookie, tag_ids=[tag_id])  # 2 in presale

        # Over-sell is now blocked up front: generating a CDK for more than the
        # presale stock is rejected, and nothing is moved out of presale.
        status, body = self._request(
            "POST",
            "/web/admin/cdks",
            payload={"count": 1, "tag_id": tag_id, "quantity": 5},  # asks for 5
            include_auth=False,
            cookie=cookie,
        )
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"], "insufficient_presale")
        unchanged = self._tag_stock(self._stock(cookie), tag_id)
        self.assertEqual(unchanged["presale"], 2)
        self.assertEqual(unchanged["available"], 0)
        self.assertEqual(unchanged["sold"], 0)

    def test_revoked_cdk_cannot_be_redeemed(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])
        created = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]
        status, _ = self._request(
            "POST", f"/web/admin/cdks/{created['id']}/revoke", payload={}, include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 200)
        status, body = self._redeem(created["code"])
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "cdk_disabled")

    def test_registered_user_redeem_binds_to_account(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]

        self.assertEqual(self._web_register("buyer", USER_PASSWORD)[0], 200)
        _, _, user_cookie = self._web_login("buyer", USER_PASSWORD)

        status, body = self._redeem(code, cookie=user_cookie)
        self.assertEqual(status, 200, body)
        address = body["mailboxes"][0]["address"]

        status, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=user_cookie)
        self.assertEqual(status, 200, mine)
        self.assertIn(address, [m["address"] for m in mine["mailboxes"]])

    def test_anonymous_redeem_does_not_bind_to_any_account(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]
        self.assertEqual(self._redeem(code)[0], 200)  # anonymous

        # A freshly registered buyer sees no mailboxes — the anonymous sale was
        # not silently linked to an account.
        self.assertEqual(self._web_register("buyer", USER_PASSWORD)[0], 200)
        _, _, user_cookie = self._web_login("buyer", USER_PASSWORD)
        status, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=user_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(mine["mailboxes"], [])

    def test_anonymous_can_read_mail_with_dispensed_credential(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]
        status, body = self._redeem(code)
        self.assertEqual(status, 200)
        credential = body["mailboxes"][0]["credential"]
        address = body["mailboxes"][0]["address"]

        self._post_inbound_mail({"to": address, "from": "svc@x.com", "subject": "code 123456", "text": "123456"})
        status, mails = self._request(
            "POST", "/web/query-mails", payload={"credential": credential}, include_auth=False
        )
        self.assertEqual(status, 200, mails)
        self.assertGreaterEqual(mails["total"], 1)

    def test_concurrent_redeem_same_code_yields_same_single_mailbox(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("only@x.com", cookie, tag_ids=[tag_id])  # exactly 1 in stock
        # A code is permanently bound to its mailbox: two concurrent redemptions
        # of the same code both succeed and both return that one mailbox — never
        # two different accounts, never more than one stock item consumed.
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]

        barrier = threading.Barrier(2)
        results: list = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            status, body = self._redeem(code)
            with lock:
                results.append((status, body))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertTrue(all(r[0] == 200 for r in results), results)
        addrs = {r[1]["mailboxes"][0]["address"] for r in results}
        self.assertEqual(addrs, {"only@x.com"})
        final = self._tag_stock(self._stock(cookie), tag_id)
        self.assertEqual(final["available"], 0)
        self.assertEqual(final["sold"], 1)

    def test_login_rate_limited_after_repeated_failures(self) -> None:
        # Repeated wrong passwords from the same client trip the limiter.
        for _ in range(5):
            status, _ = self._request(
                "POST",
                "/web/auth/login",
                payload={"username": ADMIN_USERNAME, "password": "wrong"},
                include_auth=False,
            )
            self.assertEqual(status, 401)
        status, body = self._request(
            "POST",
            "/web/auth/login",
            payload={"username": ADMIN_USERNAME, "password": "wrong"},
            include_auth=False,
        )
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], "too_many_attempts")
        # Even correct credentials are blocked while the lockout window is open.
        status, _ = self._request(
            "POST",
            "/web/auth/login",
            payload={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            include_auth=False,
        )
        self.assertEqual(status, 429)

    def test_successful_login_resets_failure_counter(self) -> None:
        # Four failures then a success clears the counter, so the next four
        # failures do not trip the limiter (would need 5 consecutive).
        for _ in range(4):
            self._request(
                "POST",
                "/web/auth/login",
                payload={"username": ADMIN_USERNAME, "password": "wrong"},
                include_auth=False,
            )
        status, _, _ = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        self.assertEqual(status, 200)
        for _ in range(4):
            status, _ = self._request(
                "POST",
                "/web/auth/login",
                payload={"username": ADMIN_USERNAME, "password": "wrong"},
                include_auth=False,
            )
            self.assertEqual(status, 401)

    def test_redeem_rate_limited_after_repeated_bad_codes(self) -> None:
        bogus = "CDK-XXXXX-XXXXX-XXXXX-XXXXX"
        for _ in range(20):
            status, body = self._redeem(bogus)
            self.assertEqual(status, 404, body)  # cdk_not_found
        status, body = self._redeem(bogus)
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], "too_many_attempts")

    def test_redeeming_valid_codes_is_not_rate_limited(self) -> None:
        # Successful redemptions must not count toward the brute-force limit:
        # a buyer redeeming many real CDKs should never be throttled.
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("\n".join(f"m{i}@x.com" for i in range(8)), cookie, tag_ids=[tag_id])
        codes = [c["code"] for c in self._gen_cdk(cookie, tag_id=tag_id, quantity=1, count=8)]
        for code in codes:
            status, body = self._redeem(code)
            self.assertEqual(status, 200, body)

    def test_weak_session_secret_logs_startup_warning(self) -> None:
        import logging as _logging
        from mail_bridge_server import MailBridgeApplication

        records: list = []

        class _Capture(_logging.Handler):
            def emit(self, record: _logging.LogRecord) -> None:
                records.append(record)

        cap = _Capture()
        cap.setLevel(_logging.WARNING)
        test_logger = _logging.getLogger("mail_bridge_weak_secret_test")
        test_logger.handlers = [cap]
        test_logger.setLevel(_logging.WARNING)
        test_logger.propagate = False

        weak = MailBridgeApplication(
            store=self.server.app.store,  # type: ignore[attr-defined]
            logger=test_logger,
            api_token="x",
            inbound_token="x",
            session_secret="CHANGE_ME_STRONG_SESSION_SECRET",
        )
        weak.warn_on_weak_config()
        self.assertTrue(any("session_secret" in r.getMessage() for r in records))

        records.clear()
        strong = MailBridgeApplication(
            store=self.server.app.store,  # type: ignore[attr-defined]
            logger=test_logger,
            api_token="x",
            inbound_token="x",
            session_secret="a-strong-random-secret-9f3a2b6c8d",
        )
        strong.warn_on_weak_config()
        self.assertEqual(records, [])

    def test_unhandled_error_in_get_returns_clean_500_json(self) -> None:
        _, _, admin_cookie = self._web_login(ADMIN_USERNAME, ADMIN_PASSWORD)
        store = self.server.app.store  # type: ignore[attr-defined]
        original = store.stock_summary_by_tag

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        store.stock_summary_by_tag = boom  # type: ignore[assignment]
        try:
            status, body = self._request(
                "GET", "/web/admin/stock", include_auth=False, cookie=admin_cookie
            )
        finally:
            store.stock_summary_by_tag = original  # type: ignore[assignment]
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "internal_error")

    def test_unhandled_error_in_post_returns_clean_500_json(self) -> None:
        store = self.server.app.store  # type: ignore[attr-defined]
        original = store.redeem_cdk

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        store.redeem_cdk = boom  # type: ignore[assignment]
        try:
            status, body = self._redeem("CDK-AAAAA-BBBBB-CCCCC-DDDDD")
        finally:
            store.redeem_cdk = original  # type: ignore[assignment]
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "internal_error")

    def test_oversized_request_body_is_rejected(self) -> None:
        import mail_bridge_server as mod

        original = mod.MAX_REQUEST_BODY_BYTES
        mod.MAX_REQUEST_BODY_BYTES = 50
        try:
            status, body = self._request(
                "POST", "/web/user/redeem", payload={"code": "X" * 500}, include_auth=False
            )
        finally:
            mod.MAX_REQUEST_BODY_BYTES = original
        self.assertEqual(status, 413)
        self.assertEqual(body["error"], "payload_too_large")

    def test_register_rejects_overlong_password(self) -> None:
        status, body = self._web_register("bigpw_user", "p" * 5000)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "password_too_long")

    def test_register_rejects_overlong_username(self) -> None:
        status, body = self._web_register("u" * 200, "goodpass123")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "username_too_long")

    def test_sales_stats_counts_totals_and_today(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com\nb@x.com\nc@x.com", cookie, tag_ids=[tag_id])
        # gen reserves 2 (presale->available); redeem sells those 2. The 3rd
        # mailbox stays in presale (never reserved), so available ends at 0.
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=2)[0]["code"]
        self.assertEqual(self._redeem(code)[0], 200)  # sells 2 of 3

        status, body = self._request("GET", "/web/admin/stats", include_auth=False, cookie=cookie)
        self.assertEqual(status, 200, body)
        stats = body["stats"]
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["sold"], 2)
        self.assertEqual(stats["available"], 0)
        self.assertEqual(stats["today_sold"], 2)
        self.assertEqual(stats["today_redemptions"], 1)

    def test_buyer_can_reset_own_mailbox_key(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]
        self.assertEqual(self._web_register("buyer", USER_PASSWORD)[0], 200)
        _, _, user_cookie = self._web_login("buyer", USER_PASSWORD)
        status, redeemed = self._redeem(code, cookie=user_cookie)
        self.assertEqual(status, 200)
        address = redeemed["mailboxes"][0]["address"]
        old_key = redeemed["mailboxes"][0]["access_key"]

        status, body = self._request(
            "POST",
            "/web/me/mailboxes/reset-key",
            payload={"address": address},
            include_auth=False,
            cookie=user_cookie,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["address"], address)
        self.assertNotEqual(body["access_key"], old_key)

        # Old credential is rejected, the new one works.
        s_old, _ = self._request(
            "POST", "/web/query-mails", payload={"credential": f"{address}----{old_key}"}, include_auth=False
        )
        self.assertEqual(s_old, 401)
        s_new, _ = self._request(
            "POST", "/web/query-mails", payload={"credential": body["credential"]}, include_auth=False
        )
        self.assertEqual(s_new, 200)

    def test_buyer_cannot_reset_unowned_mailbox(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])  # available, owner 0
        self.assertEqual(self._web_register("buyer", USER_PASSWORD)[0], 200)
        _, _, user_cookie = self._web_login("buyer", USER_PASSWORD)
        status, body = self._request(
            "POST",
            "/web/me/mailboxes/reset-key",
            payload={"address": "a@x.com"},
            include_auth=False,
            cookie=user_cookie,
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "not_owned")

    def test_reset_key_requires_login(self) -> None:
        status, _ = self._request(
            "POST", "/web/me/mailboxes/reset-key", payload={"address": "a@x.com"}, include_auth=False
        )
        self.assertEqual(status, 401)

    def test_admin_can_replace_dead_mailbox_for_account_buyer(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("dead@x.com\nspare@x.com", cookie, tag_ids=[tag_id])
        # Reserve both into the redeemable pool, then redeem only one. The other
        # stays 'available' as the spare that replace will swap in.
        codes = self._gen_cdk(cookie, tag_id=tag_id, quantity=1, count=2)
        self.assertEqual(self._web_register("buyer", USER_PASSWORD)[0], 200)
        _, _, user_cookie = self._web_login("buyer", USER_PASSWORD)
        status, redeemed = self._redeem(codes[0]["code"], cookie=user_cookie)
        self.assertEqual(status, 200)
        bad_address = redeemed["mailboxes"][0]["address"]
        bad_credential = redeemed["mailboxes"][0]["credential"]

        status, body = self._request(
            "POST", "/web/admin/mailboxes/replace", payload={"address": bad_address}, include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 200, body)
        new_address = body["address"]
        self.assertNotEqual(new_address, bad_address)

        # Dead mailbox: old credential no longer reads mail.
        s_dead, _ = self._request(
            "POST", "/web/query-mails", payload={"credential": bad_credential}, include_auth=False
        )
        self.assertEqual(s_dead, 401)
        # New credential works.
        s_new, _ = self._request(
            "POST", "/web/query-mails", payload={"credential": body["credential"]}, include_auth=False
        )
        self.assertEqual(s_new, 200)
        # Account now owns the replacement, not the dead one.
        _, mine = self._request("GET", "/web/me/mailboxes", include_auth=False, cookie=user_cookie)
        owned = [m["address"] for m in mine["mailboxes"]]
        self.assertIn(new_address, owned)
        self.assertNotIn(bad_address, owned)
        # Tag stock: both sold/dead removed from available; replacement is sold.
        stock = self._tag_stock(self._stock(cookie), tag_id)
        self.assertEqual(stock["available"], 0)
        self.assertEqual(stock["sold"], 1)

    def test_replace_rejects_unsold_mailbox(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("a@x.com", cookie, tag_ids=[tag_id])  # available, never sold
        status, body = self._request(
            "POST", "/web/admin/mailboxes/replace", payload={"address": "a@x.com"}, include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "not_sold")

    def test_replace_without_spare_stock_is_conflict(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        self._bulk_import("only@x.com", cookie, tag_ids=[tag_id])  # one, gets sold
        code = self._gen_cdk(cookie, tag_id=tag_id, quantity=1)[0]["code"]
        self.assertEqual(self._redeem(code)[0], 200)
        status, body = self._request(
            "POST", "/web/admin/mailboxes/replace", payload={"address": "only@x.com"}, include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "insufficient_stock")

    def test_cdk_list_filters_by_tag(self) -> None:
        cookie = self._admin_cookie()
        t1 = self._create_tag("outlook", cookie)
        t2 = self._create_tag("gmail", cookie)
        # Each CDK reserves a presale mailbox, so stock it first.
        self._bulk_import("o1@x.com\no2@x.com", cookie, tag_ids=[t1])
        self._bulk_import("g1@x.com\ng2@x.com\ng3@x.com", cookie, tag_ids=[t2])
        self._gen_cdk(cookie, tag_id=t1, quantity=1, count=2)
        self._gen_cdk(cookie, tag_id=t2, quantity=1, count=3)

        status, body = self._request(
            "GET", f"/web/admin/cdks?tag_id={t1}", include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 2)
        self.assertTrue(all(c["tag_id"] == t1 for c in body["cdks"]))

    def test_cdk_list_filters_by_keyword_batch(self) -> None:
        cookie = self._admin_cookie()
        tag_id = self._create_tag("outlook", cookie)
        # 3 presale: 2 for the march batch + 1 for the unlabelled CDK.
        self._bulk_import("a@x.com\nb@x.com\nc@x.com", cookie, tag_ids=[tag_id])
        self._request(
            "POST",
            "/web/admin/cdks",
            payload={"count": 2, "tag_id": tag_id, "quantity": 1, "batch_label": "march-batch"},
            include_auth=False,
            cookie=cookie,
        )
        self._gen_cdk(cookie, tag_id=tag_id, quantity=1, count=1)  # no batch
        status, body = self._request(
            "GET", "/web/admin/cdks?keyword=march", include_auth=False, cookie=cookie
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 2)

    def test_public_page_serves_redeem_tab_and_local_cache(self) -> None:
        status, raw, _ = self._raw_request("GET", "/web/query", include_auth=False)
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        for marker in (
            'data-tab-btn="redeem"',
            'id="cdk-code"',
            'id="my-mailbox-list"',
            "MB_LS_KEY",
            'id="auto-refresh"',
            "startAutoRefresh",
        ):
            self.assertIn(marker, html)

    def test_email_host_query_redirects_to_icloud_query(self) -> None:
        status, _, headers = self._raw_request(
            "GET",
            "/web/query",
            include_auth=False,
            host="email.52moyu.net",
        )
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "https://icloud.52moyu.net/web/query")

    def test_icloud_host_query_still_serves_public_page(self) -> None:
        status, raw, _ = self._raw_request(
            "GET",
            "/web/query",
            include_auth=False,
            host="icloud.52moyu.net",
        )
        self.assertEqual(status, 200)
        self.assertIn('data-tab-btn="redeem"', raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
