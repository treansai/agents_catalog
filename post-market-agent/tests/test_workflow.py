import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class WorkflowTests(unittest.TestCase):
    def test_qualify_lead_returns_structured_sales_decision(self):
        from workflow import qualify_lead

        result = qualify_lead(
            lead={
                "name": "Maya Chen",
                "email": "maya.chen@examplecorp.com",
                "company": "Example Corp.",
                "job_title": "VP of Operations",
                "country": "United States",
                "message": "We're evaluating workflow automation for our support ops team.",
                "source": "demo_request",
            },
            behavior={
                "pages_viewed": ["/pricing", "/enterprise", "/case-studies/support-automation"],
                "email_engagement": {
                    "opened_last_campaign": True,
                    "clicked_pricing_link": True,
                },
                "product_usage": {"active_users": 8, "created_workflows": 12},
            },
            enrichment={
                "company_domain": "examplecorp.com",
                "industry": "B2B SaaS",
                "employee_count": 1200,
                "hq_country": "United States",
                "tech_stack": ["Salesforce", "Zendesk"],
                "target_account": True,
                "existing_customer": False,
                "open_opportunity": False,
            },
        )

        self.assertEqual(result["normalized_lead"]["domain"], "examplecorp.com")
        self.assertEqual(result["normalized_lead"]["seniority"], "executive")
        self.assertEqual(result["classification"]["status"], "sales_qualified")
        self.assertEqual(result["classification"]["priority"], "high")
        self.assertGreaterEqual(result["scores"]["overall"], 80)
        self.assertTrue(result["visible_reasoning_trace"])
        self.assertIn("budget", result["missing_information"])

    def test_workflow_routes_existing_customer_before_new_business(self):
        from workflow import qualify_lead

        result = qualify_lead(
            lead={
                "name": "Maya Chen",
                "email": "maya.chen@examplecorp.com",
                "company": "ExampleCorp",
                "job_title": "VP of Operations",
                "country": "United States",
                "message": "We want to expand workflow automation.",
                "source": "demo_request",
            },
            enrichment={
                "company_domain": "examplecorp.com",
                "industry": "B2B SaaS",
                "employee_count": 1200,
                "target_account": True,
                "existing_customer": True,
                "open_opportunity": False,
                "account_owner": "csm@example.com",
            },
        )

        self.assertEqual(result["classification"]["status"], "customer_expansion")
        self.assertEqual(result["routing"]["team"], "Customer Success")
        self.assertTrue(result["audit"]["human_review_required"])

    def test_build_graph_exposes_langgraph_invoke_api(self):
        from workflow import build_lead_qualification_graph

        graph = build_lead_qualification_graph()
        result = graph.invoke(
            {
                "lead": {
                    "name": "Ari Smith",
                    "email": "ari@gmail.com",
                    "company": "",
                    "job_title": "Manager",
                    "country": "United States",
                    "message": "Just browsing.",
                    "source": "contact_us",
                },
                "behavior": {},
                "enrichment": {},
            }
        )

        self.assertIn("final_output", result)
        self.assertEqual(result["final_output"]["classification"]["status"], "disqualified")
        self.assertLess(result["final_output"]["scores"]["confidence"], 0.8)


if __name__ == "__main__":
    unittest.main()
