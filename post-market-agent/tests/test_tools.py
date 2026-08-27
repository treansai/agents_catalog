import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class JsonHandler(BaseHTTPRequestHandler):
    calls = []

    def do_GET(self):
        self.__class__.calls.append(
            {
                "method": "GET",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "path": self.path}).encode())

    def log_message(self, *_args):
        return


class ToolTests(unittest.TestCase):
    def test_http_tool_requires_real_endpoint_configuration(self):
        from tools import ToolConfigurationError, lookup_crm_contact

        old_base_url = os.environ.pop("CRM_API_BASE_URL", None)
        old_api_key = os.environ.pop("CRM_API_KEY", None)
        try:
            with self.assertRaises(ToolConfigurationError):
                lookup_crm_contact.invoke({"email": "maya.chen@examplecorp.com"})
        finally:
            if old_base_url is not None:
                os.environ["CRM_API_BASE_URL"] = old_base_url
            if old_api_key is not None:
                os.environ["CRM_API_KEY"] = old_api_key

    def test_http_tool_calls_configured_provider(self):
        from tools import lookup_crm_contact

        server = HTTPServer(("127.0.0.1", 0), JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        old_base_url = os.environ.get("CRM_API_BASE_URL")
        old_api_key = os.environ.get("CRM_API_KEY")
        JsonHandler.calls = []
        os.environ["CRM_API_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
        os.environ["CRM_API_KEY"] = "secret"
        try:
            result = lookup_crm_contact.invoke({"email": "maya.chen@examplecorp.com"})
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
            if old_base_url is None:
                os.environ.pop("CRM_API_BASE_URL", None)
            else:
                os.environ["CRM_API_BASE_URL"] = old_base_url
            if old_api_key is None:
                os.environ.pop("CRM_API_KEY", None)
            else:
                os.environ["CRM_API_KEY"] = old_api_key

        self.assertTrue(result["ok"])
        self.assertEqual(JsonHandler.calls[0]["path"], "/contacts?email=maya.chen%40examplecorp.com")
        self.assertEqual(JsonHandler.calls[0]["authorization"], "Bearer secret")

    def test_score_lead_returns_auditable_components_without_inventing_data(self):
        from tools import score_lead

        result = score_lead.invoke(
            {
                "lead": {
                    "email": "maya.chen@examplecorp.com",
                    "job_title": "VP of Operations",
                    "country": "United States",
                    "message": "We need workflow automation for support ops.",
                    "source": "demo_request",
                },
                "enrichment": {
                    "employee_count": 1200,
                    "industry": "B2B SaaS",
                    "target_account": True,
                    "tech_stack": ["Salesforce", "Zendesk"],
                    "existing_customer": False,
                    "open_opportunity": False,
                },
                "behavior": {
                    "pages_viewed": ["/pricing", "/enterprise"],
                    "email_engagement": {"clicked_pricing_link": True},
                    "product_usage": {"active_users": 8, "created_workflows": 12},
                },
            }
        )

        self.assertGreaterEqual(result["overall"], 80)
        self.assertGreaterEqual(result["confidence"], 0.75)
        self.assertEqual(result["missing_information"], ["budget", "timeline", "current vendor"])
        self.assertTrue(any(item["signal"] == "Target account" for item in result["fit_factors"]))
        self.assertTrue(any(item["signal"] == "Demo request" for item in result["intent_factors"]))

    def test_route_lead_prioritizes_account_conflicts_before_score(self):
        from tools import route_lead

        result = route_lead.invoke(
            {
                "score": 95,
                "employee_count": 1200,
                "region": "North America",
                "existing_customer": False,
                "open_opportunity": True,
                "account_owner": "ae@example.com",
                "target_account": True,
                "product_interest": "workflow automation",
            }
        )

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["team"], "Current Opportunity Owner")
        self.assertEqual(result["owner"], "ae@example.com")
        self.assertTrue(result["human_review_required"])


if __name__ == "__main__":
    unittest.main()
