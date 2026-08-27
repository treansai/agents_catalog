import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))


class ModelAndCliTests(unittest.TestCase):
    def test_output_model_validates_workflow_response(self):
        from models import LeadQualificationOutput
        from workflow import qualify_lead

        output = qualify_lead(
            lead={
                "name": "Maya Chen",
                "email": "maya.chen@examplecorp.com",
                "company": "ExampleCorp",
                "job_title": "VP of Operations",
                "country": "United States",
                "message": "Evaluating workflow automation.",
                "source": "demo_request",
            },
            enrichment={
                "company_domain": "examplecorp.com",
                "industry": "B2B SaaS",
                "employee_count": 1200,
                "target_account": True,
            },
        )

        parsed = LeadQualificationOutput.model_validate(output)

        self.assertEqual(parsed.normalized_lead.email, "maya.chen@examplecorp.com")
        self.assertGreaterEqual(parsed.scores.overall, 0)

    def test_output_model_rejects_invalid_classification_status(self):
        from models import Classification, Priority

        with self.assertRaises(ValidationError):
            Classification(
                status="almost_qualified",
                priority=Priority.high,
                reason_summary="invalid status should fail",
            )

    def test_cli_qualifies_lead_payload_from_json_file(self):
        payload = {
            "lead": {
                "name": "Maya Chen",
                "email": "maya.chen@examplecorp.com",
                "company": "ExampleCorp",
                "job_title": "VP of Operations",
                "country": "United States",
                "message": "Evaluating workflow automation.",
                "source": "demo_request",
            },
            "behavior": {
                "pages_viewed": ["/pricing"],
                "email_engagement": {"clicked_pricing_link": True},
            },
            "enrichment": {
                "company_domain": "examplecorp.com",
                "industry": "B2B SaaS",
                "employee_count": 1200,
                "target_account": True,
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            input_path = handle.name

        try:
            completed = subprocess.run(
                [sys.executable, "__main__.py", input_path],
                cwd=PROJECT_DIR,
                check=True,
                text=True,
                capture_output=True,
            )
        finally:
            Path(input_path).unlink(missing_ok=True)

        output = json.loads(completed.stdout)
        self.assertEqual(output["normalized_lead"]["domain"], "examplecorp.com")
        self.assertEqual(output["classification"]["status"], "sales_qualified")


if __name__ == "__main__":
    unittest.main()
