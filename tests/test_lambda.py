import json

import pytest

from unittest.mock import patch, MagicMock

import sys

import os

# ─────────────────────────────────────────────

# Make lambda_function importable from project root

# ─────────────────────────────────────────────

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Mock environment variables BEFORE importing lambda_function

os.environ.setdefault("S3_BUCKET", "test-bucket")

os.environ.setdefault("SES_SENDER_EMAIL", "sender@example.com")

os.environ.setdefault("SES_RECIPIENT_EMAILS", "recipient@example.com")

os.environ.setdefault("SES_REGION", "us-east-1")

import lambda_function as lf

# ═════════════════════════════════════════════

# 1. TESTS — clean_md()

# ═════════════════════════════════════════════


class TestCleanMd:

    def test_removes_bold_markdown(self):

        result = lf.clean_md("**bold text**")

        assert "**" not in result

        assert "bold text" in result

    def test_removes_italic_markdown(self):

        result = lf.clean_md("*italic text*")

        assert "*" not in result

        assert "italic text" in result

    def test_removes_heading_markdown(self):

        result = lf.clean_md("## Section Title")

        assert "#" not in result

        assert "Section Title" in result

    def test_returns_na_for_empty_string(self):

        result = lf.clean_md("")

        assert result == "N/A"

    def test_returns_na_for_na_input(self):

        result = lf.clean_md("N/A")

        assert result == "N/A"

    def test_removes_emoji_characters(self):

        result = lf.clean_md("🔴 Critical issue")

        assert "🔴" not in result

    def test_plain_text_unchanged(self):

        result = lf.clean_md("plain text")

        assert result == "plain text"

    def test_none_input(self):

        result = lf.clean_md(None)

        assert result == "N/A"


# ═════════════════════════════════════════════

# 2. TESTS — trunc()

# ═════════════════════════════════════════════


class TestTrunc:

    def test_truncates_long_text(self):

        result = lf.trunc("hello world", 5)

        assert result == "hello…"

    def test_does_not_truncate_short_text(self):

        result = lf.trunc("hello", 10)

        assert result == "hello"

    def test_exact_length_not_truncated(self):

        result = lf.trunc("hello", 5)

        assert result == "hello"

    def test_empty_string(self):

        result = lf.trunc("", 5)

        assert result == ""


# ═════════════════════════════════════════════

# 3. TESTS — parse_change_counts()

# ═════════════════════════════════════════════


class TestParseChangeCounts:

    def setup_method(self):

        self.sample_summary = """

        | **Additions**     | 3  |

        | **Deletions**     | 1  |

        | **Modifications** | 5  |

        | **Relocations**   | 2  |

        | **TOTAL CHANGES** | **11** |

        """

    def test_parses_additions(self):

        counts = lf.parse_change_counts(self.sample_summary)

        assert counts["Additions"] == 3

    def test_parses_deletions(self):

        counts = lf.parse_change_counts(self.sample_summary)

        assert counts["Deletions"] == 1

    def test_parses_modifications(self):

        counts = lf.parse_change_counts(self.sample_summary)

        assert counts["Modifications"] == 5

    def test_parses_relocations(self):

        counts = lf.parse_change_counts(self.sample_summary)

        assert counts["Relocations"] == 2

    def test_parses_total(self):

        counts = lf.parse_change_counts(self.sample_summary)

        assert counts["Total"] == 11

    def test_returns_zeros_for_empty_summary(self):

        counts = lf.parse_change_counts("")

        assert counts == {
            "Additions": 0,
            "Deletions": 0,
            "Modifications": 0,
            "Relocations": 0,
            "Total": 0,
        }


# ═════════════════════════════════════════════

# 4. TESTS — parse_risk_level()

# ═════════════════════════════════════════════


class TestParseRiskLevel:

    def test_detects_critical(self):

        level, color, emoji = lf.parse_risk_level("RISK LEVEL: CRITICAL")

        assert level == "CRITICAL"

        assert color == "#dc2626"

        assert emoji == "🔴"

    def test_detects_high(self):

        level, color, emoji = lf.parse_risk_level("RISK LEVEL: HIGH")

        assert level == "HIGH"

        assert color == "#ea580c"

        assert emoji == "🟠"

    def test_detects_medium(self):

        level, color, emoji = lf.parse_risk_level("RISK LEVEL: MEDIUM")

        assert level == "MEDIUM"

        assert color == "#d97706"

        assert emoji == "🟡"

    def test_detects_low(self):

        level, color, emoji = lf.parse_risk_level("RISK LEVEL: LOW")

        assert level == "LOW"

        assert color == "#16a34a"

        assert emoji == "🟢"

    def test_case_insensitive(self):

        level, color, emoji = lf.parse_risk_level("risk level: critical")

        assert level == "CRITICAL"

    def test_not_assessed_when_missing(self):

        level, color, emoji = lf.parse_risk_level("No risk info here")

        assert level == "NOT ASSESSED"

        assert color == "#64748b"


# ═════════════════════════════════════════════

# 5. TESTS — parse_individual_changes()

# ═════════════════════════════════════════════


class TestParseIndividualChanges:

    def setup_method(self):

        self.sample_summary = """

        **Change 1**

        | Section        | Section 4.2         |

        | Change Type    | CRITICAL            |

        | Modified Status| Updated             |

        | Previous       | Old procedure text  |

        | Current        | New procedure text  |

        | Impact         | High patient safety |
 
        **Change 2**

        | Section        | Section 5.1         |

        | Change Type    | PROCEDURAL          |

        | Modified Status| Added               |

        | Previous       | N/A                 |

        | Current        | New content added   |

        | Impact         | Low impact          |

        """

    def test_parses_two_changes(self):

        changes = lf.parse_individual_changes(self.sample_summary)

        assert len(changes) == 2

    def test_first_change_number(self):

        changes = lf.parse_individual_changes(self.sample_summary)

        assert changes[0]["num"] == "1"

    def test_first_change_section(self):

        changes = lf.parse_individual_changes(self.sample_summary)

        assert "4.2" in changes[0]["section"]

    def test_second_change_number(self):

        changes = lf.parse_individual_changes(self.sample_summary)

        assert changes[1]["num"] == "2"

    def test_empty_summary_returns_empty_list(self):

        changes = lf.parse_individual_changes("")

        assert changes == []


# ═════════════════════════════════════════════

# 6. TESTS — risk_style()

# ═════════════════════════════════════════════


class TestRiskStyle:

    def test_critical_change_type(self):

        color, bg, badge = lf.risk_style("CRITICAL")

        assert color == "#dc2626"

        assert "CRITICAL" in badge

    def test_safety_change_type(self):

        color, bg, badge = lf.risk_style("SAFETY")

        assert color == "#ea580c"

        assert "SAFETY" in badge

    def test_procedural_change_type(self):

        color, bg, badge = lf.risk_style("PROCEDURAL")

        assert color == "#d97706"

        assert "PROCEDURAL" in badge

    def test_unknown_change_type(self):

        color, bg, badge = lf.risk_style("UNKNOWN")

        assert color == "#64748b"

        assert badge == "NONE"

    def test_case_insensitive(self):

        color, bg, badge = lf.risk_style("critical")

        assert color == "#dc2626"


# ═════════════════════════════════════════════

# 7. TESTS — build_html_report()

# ═════════════════════════════════════════════


class TestBuildHtmlReport:

    def setup_method(self):

        self.summary = """

        | **Additions**     | 2  |

        | **Deletions**     | 1  |

        | **Modifications** | 1  |

        | **Relocations**   | 0  |

        | **TOTAL CHANGES** | **4** |

        RISK LEVEL: HIGH

        """

    def test_returns_html_string(self):

        html = lf.build_html_report(
            self.summary,
            "DOC-001",
            "Test SOP",
            "v2.0",
            "v1.0",
            "John Doe",
            "2024-01-01",
        )

        assert isinstance(html, str)

    def test_contains_doctype(self):

        html = lf.build_html_report(
            self.summary,
            "DOC-001",
            "Test SOP",
            "v2.0",
            "v1.0",
            "John Doe",
            "2024-01-01",
        )

        assert "<!DOCTYPE html>" in html

    def test_contains_doc_id(self):

        html = lf.build_html_report(
            self.summary,
            "DOC-001",
            "Test SOP",
            "v2.0",
            "v1.0",
            "John Doe",
            "2024-01-01",
        )

        assert "DOC-001" in html

    def test_contains_doc_name(self):

        html = lf.build_html_report(
            self.summary,
            "DOC-001",
            "Test SOP",
            "v2.0",
            "v1.0",
            "John Doe",
            "2024-01-01",
        )

        assert "Test SOP" in html

    def test_contains_version_change(self):

        html = lf.build_html_report(
            self.summary,
            "DOC-001",
            "Test SOP",
            "v2.0",
            "v1.0",
            "John Doe",
            "2024-01-01",
        )

        assert "v1.0" in html

        assert "v2.0" in html

    def test_contains_approved_by(self):

        html = lf.build_html_report(
            self.summary,
            "DOC-001",
            "Test SOP",
            "v2.0",
            "v1.0",
            "John Doe",
            "2024-01-01",
        )

        assert "John Doe" in html


# ═════════════════════════════════════════════

# 8. TESTS — lambda_handler()

# ═════════════════════════════════════════════


class TestLambdaHandler:

    def setup_method(self):

        self.event = {
            "actionGroup": "EmailActionGroup",
            "apiPath": "/send-summary-email",
            "httpMethod": "POST",
            "requestBody": {
                "content": {
                    "application/json": {
                        "properties": [
                            {
                                "name": "summary_s3_path",
                                "value": "s3://test-bucket/summary.txt",
                            },
                            {
                                "name": "summary_json_path",
                                "value": "s3://test-bucket/summary.json",
                            },
                        ]
                    }
                }
            },
        }

        self.mock_summary = b"""

        | **Additions**     | 2  |

        | **Deletions**     | 1  |

        | **Modifications** | 1  |

        | **Relocations**   | 0  |

        | **TOTAL CHANGES** | **4** |

        RISK LEVEL: HIGH

        **Change 1**

        | Section        | Section 4.2        |

        | Change Type    | CRITICAL           |

        | Modified Status| Updated            |

        | Previous       | Old text           |

        | Current        | New text           |

        | Impact         | High impact        |

        """

        self.mock_metadata = json.dumps(
            {
                "doc_id": "DOC-001",
                "doc_name": "Test SOP",
                "doc_version": "v2.0",
                "prev_version": "v1.0",
                "approved_by": "John Doe",
                "approval_date": "2024-01-01",
            }
        ).encode("utf-8")

    @patch("lambda_function.ses_client")
    @patch("lambda_function.s3_client")
    def test_successful_handler_returns_200(self, mock_s3, mock_ses):

        mock_s3.get_object.side_effect = [
            {"Body": MagicMock(read=lambda: self.mock_summary)},
            {"Body": MagicMock(read=lambda: self.mock_metadata)},
        ]

        mock_ses.send_raw_email.return_value = {"MessageId": "test-id"}

        result = lf.lambda_handler(self.event, {})

        assert result["response"]["httpStatusCode"] == 200

    @patch("lambda_function.ses_client")
    @patch("lambda_function.s3_client")
    def test_handler_returns_correct_action_group(self, mock_s3, mock_ses):

        mock_s3.get_object.side_effect = [
            {"Body": MagicMock(read=lambda: self.mock_summary)},
            {"Body": MagicMock(read=lambda: self.mock_metadata)},
        ]

        mock_ses.send_raw_email.return_value = {"MessageId": "test-id"}

        result = lf.lambda_handler(self.event, {})

        assert result["response"]["actionGroup"] == "EmailActionGroup"

    @patch("lambda_function.ses_client")
    @patch("lambda_function.s3_client")
    def test_handler_returns_success_status(self, mock_s3, mock_ses):

        mock_s3.get_object.side_effect = [
            {"Body": MagicMock(read=lambda: self.mock_summary)},
            {"Body": MagicMock(read=lambda: self.mock_metadata)},
        ]

        mock_ses.send_raw_email.return_value = {"MessageId": "test-id"}

        result = lf.lambda_handler(self.event, {})

        body_str = result["response"]["responseBody"]["application/json"]["body"]

        body = json.loads(body_str)

        assert body["status"] == "success"

    @patch("lambda_function.s3_client")
    def test_handler_returns_500_on_missing_params(self, mock_s3):

        bad_event = {
            "actionGroup": "EmailActionGroup",
            "apiPath": "/send-summary-email",
            "httpMethod": "POST",
            "requestBody": {"content": {"application/json": {"properties": []}}},
        }

        result = lf.lambda_handler(bad_event, {})

        assert result["response"]["httpStatusCode"] == 500

    @patch("lambda_function.ses_client")
    @patch("lambda_function.s3_client")
    def test_handler_parses_from_parameters_fallback(self, mock_s3, mock_ses):

        fallback_event = {
            "actionGroup": "EmailActionGroup",
            "apiPath": "/send-summary-email",
            "httpMethod": "POST",
            "parameters": [
                {"name": "summary_s3_path", "value": "s3://test-bucket/summary.txt"},
                {"name": "summary_json_path", "value": "s3://test-bucket/summary.json"},
            ],
        }

        mock_s3.get_object.side_effect = [
            {"Body": MagicMock(read=lambda: self.mock_summary)},
            {"Body": MagicMock(read=lambda: self.mock_metadata)},
        ]

        mock_ses.send_raw_email.return_value = {"MessageId": "test-id"}

        result = lf.lambda_handler(fallback_event, {})

        assert result["response"]["httpStatusCode"] == 200
