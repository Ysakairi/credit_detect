"""Schema catalog stays aligned with the current Dataform pipeline."""

import unittest

from agent.config import AgentConfig, schema_prompt


class SchemaCatalogTest(unittest.TestCase):
    def test_schema_prefers_regional_public_copy(self):
        cfg = AgentConfig(project_id="skir-sample-credit")
        text = schema_prompt(cfg)
        self.assertIn(
            "skir-sample-credit.dwh_prod.ulb_fraud_detection_public",
            text,
        )
        self.assertIn("ulb_fraud_detection_predictions", text)
        self.assertIn("ulb_fraud_detection_feature_matrix", text)
        self.assertIn("fraud_probability", text)


if __name__ == "__main__":
    unittest.main()
