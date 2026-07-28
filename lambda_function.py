import json
import logging
import os
import re
from datetime import UTC, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")
ses_client = boto3.client("ses", region_name=os.environ.get("SES_REGION", "us-east-1"))

S3_BUCKET = os.environ["S3_BUCKET"]
SENDER_EMAIL = os.environ["SES_SENDER_EMAIL"]
RECIPIENT_EMAILS = [
    email.strip()
    for email in os.environ.get("SES_RECIPIENT_EMAILS", "").split(",")
    if email.strip()
]


# ─────────────────────────────────────────────
# HELPER: Build OpenAPI Response (FIXED)
# ─────────────────────────────────────────────
def build_response(event, body_dict, status_code=200):
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup"),
            "apiPath": event.get("apiPath"),  # ← KEY FIX
            "httpMethod": event.get("httpMethod"),  # ← KEY FIX
            "httpStatusCode": status_code,  # ← KEY FIX
            "responseBody": {
                "application/json": {"body": json.dumps(body_dict)}  # ← KEY FIX
            },
        },
    }


# ─────────────────────────────────────────────
# HELPER: Parse S3 URI
# ─────────────────────────────────────────────
def parse_s3_uri(s3_uri):
    s3_uri = s3_uri.replace("s3://", "")
    parts = s3_uri.split("/", 1)
    return parts[0], parts[1]


def read_from_s3(s3_path):
    bucket, key = parse_s3_uri(s3_path)
    logger.info(f"Reading from s3://{bucket}/{key}")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


# ─────────────────────────────────────────────
# HELPER: Strip markdown characters for HTML display
# ─────────────────────────────────────────────
def clean_md(text):
    if not text or text.strip() in ("N/A", ""):
        return text or "N/A"
    text = re.sub(r"\|[\s\-|:]+\|", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^\s*\||\|\s*$", "", text.strip())
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[🔴🟠🟡🟢🚩📋⚠✅📎]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "N/A"


# ─────────────────────────────────────────────
# HELPER: Truncate long text
# ─────────────────────────────────────────────
def trunc(text, n):
    return (text[:n] + "…") if len(text) > n else text


# ─────────────────────────────────────────────
# PARSERS: Extract structured data from markdown
# ─────────────────────────────────────────────
def parse_change_counts(summary):
    counts = {
        "Additions": 0,
        "Deletions": 0,
        "Modifications": 0,
        "Relocations": 0,
        "Total": 0,
    }
    patterns = {
        "Additions": r"\|\s*\*{0,2}Additions\*{0,2}\s*\|\s*(\d+)",
        "Deletions": r"\|\s*\*{0,2}Deletions\*{0,2}\s*\|\s*(\d+)",
        "Modifications": r"\|\s*\*{0,2}Modifications\*{0,2}\s*\|\s*(\d+)",
        "Relocations": r"\|\s*\*{0,2}Relocations\*{0,2}\s*\|\s*(\d+)",
        "Total": r"\|\s*\*{0,2}TOTAL CHANGES\*{0,2}\s*\|\s*\*{0,2}(\d+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, summary, re.IGNORECASE)
        if m:
            counts[key] = int(m.group(1))
    return counts


def parse_risk_level(summary):
    m = re.search(
        r"RISK LEVEL[:\s🔴🟠🟡🟢⚪\s]*(CRITICAL|HIGH|MEDIUM|LOW)",
        summary,
        re.IGNORECASE,
    )
    if m:
        level = m.group(1).upper()
        styles = {
            "CRITICAL": ("#dc2626", "🔴"),
            "HIGH": ("#ea580c", "🟠"),
            "MEDIUM": ("#d97706", "🟡"),
            "LOW": ("#16a34a", "🟢"),
        }
        color, emoji = styles.get(level, ("#64748b", "⚪"))
        return level, color, emoji
    return "NOT ASSESSED", "#64748b", "⚪"


def parse_individual_changes(summary):
    changes = []
    blocks = re.split(r"\*\*Change\s+(\d+)\*\*", summary)
    i = 1
    while i < len(blocks) - 1:
        num = blocks[i]
        content = blocks[i + 1]

        def get_field(field, text):
            m = re.search(
                rf"\|\s*\*{{0,2}}{re.escape(field)}\*{{0,2}}\s*\|\s*([^|\n]+)",
                text,
                re.IGNORECASE,
            )
            return clean_md(m.group(1).strip()) if m else "N/A"

        changes.append(
            {
                "num": num,
                "section": get_field("Section", content),
                "change_type": get_field("Change Type", content),
                "modified_status": get_field("Modified Status", content),
                "previous": get_field("Previous", content),
                "current": get_field("Current", content),
                "impact": get_field("Impact", content),
            }
        )
        i += 2
    return changes


def risk_style(change_type):
    ct = change_type.upper()
    if "CRITICAL" in ct:
        return "#dc2626", "#fef2f2", "🔴 CRITICAL"
    if "SAFETY" in ct:
        return "#ea580c", "#fff7ed", "🟠 SAFETY"
    if "PROCEDURAL" in ct:
        return "#d97706", "#fffbeb", "🟡 PROCEDURAL"
    return "#64748b", "#f8fafc", "NONE"


# ─────────────────────────────────────────────
# BUILD HTML REPORT
# ─────────────────────────────────────────────
def build_html_report(
    summary, doc_id, doc_name, doc_version, prev_version, approved_by, approval_date
):

    counts = parse_change_counts(summary)
    _risk_level, _risk_color, _risk_emoji = parse_risk_level(summary)
    changes = parse_individual_changes(summary)
    generated_at = datetime.now(UTC).strftime("%B %d, %Y at %I:%M %p UTC")

    cards_html = ""
    for ch in changes:
        border_color, bg_color, badge_text = risk_style(ch["change_type"])
        prev_display = ch["previous"]
        curr_display = ch["current"]
        impact_display = ch["impact"]

        cards_html += f"""
        <div style="border:1px solid #e2e8f0; border-left:4px solid {border_color}; border-radius:6px; margin-bottom:14px; overflow:hidden;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr style="background:{bg_color};">
              <td style="padding:12px 16px; vertical-align:top;">
                <span style="font-weight:700; font-size:14px; color:#0f172a; display:block; white-space:normal; word-wrap:break-word;">
                  Change {ch['num']} — {ch['section']}
                </span>
              </td>
              <td align="right" style="padding:12px 16px; white-space:nowrap; vertical-align:top;">
                <span style="background:{border_color}; color:white; padding:3px 10px; border-radius:4px; font-size:11px; font-weight:700;">
                  {badge_text}
                </span>
                &nbsp;
                <span style="background:#e2e8f0; color:#475569; padding:3px 10px; border-radius:4px; font-size:11px; font-weight:600;">
                  {ch['modified_status']}
                </span>
              </td>
            </tr>
          </table>
          <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px; border-top:1px solid #f1f5f9;">
            <tr>
              <td style="padding:9px 16px; font-weight:600; color:#64748b; background:#f8fafc; width:150px; vertical-align:top;">Previous</td>
              <td style="padding:9px 16px; color:#94a3b8; font-style:italic; vertical-align:top;">{prev_display}</td>
            </tr>
            <tr>
              <td style="padding:9px 16px; font-weight:600; color:#64748b; background:#f8fafc; vertical-align:top;">Current</td>
              <td style="padding:9px 16px; color:#0f172a; vertical-align:top;">{curr_display}</td>
            </tr>
            <tr>
              <td style="padding:9px 16px; font-weight:600; color:#64748b; background:#f8fafc; vertical-align:top;">Impact</td>
              <td style="padding:9px 16px; color:#1e3a5f; vertical-align:top;">{impact_display}</td>
            </tr>
          </table>
        </div>"""

    if not cards_html:
        cards_html = """
        <div style="padding:20px; text-align:center; color:#64748b;
                    background:#f8fafc; border-radius:6px; font-size:13px;">
          No individual changes could be parsed. Please refer to the full summary in S3.
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>GxP Change Summary — {doc_name or doc_id}</title>
</head>
<body style="margin:0; padding:0; background:#dce3ec; font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#dce3ec; padding:24px 0;">
<tr><td align="center">
<table width="700" cellpadding="0" cellspacing="0"
       style="background:white; border:1px solid #c8d0da; border-radius:10px;
              overflow:hidden; box-shadow:0 6px 24px rgba(0,0,0,0.12);">
 
  <!-- HEADER -->
  <tr>
    <td style="background:#0f2d52; padding:26px 30px;">
      <div style="font-size:20px; font-weight:700; color:white; margin-bottom:5px;">
        GxP SOP Change Control Summary
      </div>
      <div style="font-size:12px; color:#90b4d8;">
        Enterprise Quality — GxP Document Comparison &amp; Change Control Notification
      </div>
    </td>
  </tr>
 
  <!-- DOCUMENT META -->
  <tr>
    <td style="padding:16px 30px; background:white; border-bottom:1px solid #e2e8f0;">
      <table width="100%" cellpadding="0" cellspacing="4">
        <tr>
          <td style="font-size:13px; color:#334155; padding:2px 0;">
            <strong>Document Name:</strong> {doc_name or 'N/A'}
          </td>
          <td style="font-size:13px; color:#334155; padding:2px 0;">
            <strong>Document ID:</strong> {doc_id}
          </td>
        </tr>
        <tr>
          <td style="font-size:13px; color:#334155; padding:2px 0;">
            <strong>Version Change:</strong>
            <span style="background:#eff6ff; color:#1d4ed8; padding:2px 8px;
                         border-radius:4px; font-weight:600;">
              {prev_version} → {doc_version}
            </span>
          </td>
          <td style="font-size:13px; color:#334155; padding:2px 0;">
            <strong>Approved By:</strong> {approved_by or 'N/A'}
          </td>
        </tr>
        <tr>
          <td style="font-size:13px; color:#334155; padding:2px 0;">
            <strong>Approval Date:</strong> {approval_date or 'N/A'}
          </td>
          <td style="font-size:11px; color:#94a3b8; padding:2px 0;">
            Generated: {generated_at}
          </td>
        </tr>
      </table>
    </td>
  </tr>
 
  <!-- EXECUTIVE SUMMARY -->
  <tr>
    <td style="padding:20px 30px; background:#f8fafc; border-bottom:1px solid #e2e8f0;">
      <div style="font-size:15px; font-weight:700; color:#1e3a5f; margin-bottom:16px;">
        Executive Summary
      </div>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td width="20%" align="center" style="padding-bottom:14px;">
            <div style="font-size:28px; font-weight:800; color:#0f172a;">{counts['Total']}</div>
            <div style="font-size:11px; color:#64748b; margin-top:2px;">Total Changes</div>
          </td>
          <td width="20%" align="center" style="padding-bottom:14px;">
            <div style="font-size:28px; font-weight:800; color:#16a34a;">{counts['Additions']}</div>
            <div style="font-size:11px; color:#64748b; margin-top:2px;">Additions</div>
          </td>
          <td width="20%" align="center" style="padding-bottom:14px;">
            <div style="font-size:28px; font-weight:800; color:#dc2626;">{counts['Deletions']}</div>
            <div style="font-size:11px; color:#64748b; margin-top:2px;">Deletions</div>
          </td>
          <td width="20%" align="center" style="padding-bottom:14px;">
            <div style="font-size:28px; font-weight:800; color:#d97706;">{counts['Modifications']}</div>
            <div style="font-size:11px; color:#64748b; margin-top:2px;">Modifications</div>
          </td>
          <td width="20%" align="center" style="padding-bottom:14px;">
            <div style="font-size:28px; font-weight:800; color:#6366f1;">{counts['Relocations']}</div>
            <div style="font-size:11px; color:#64748b; margin-top:2px;">Relocations</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
 
  <!-- CHANGE DETAILS -->
  <tr>
    <td style="padding:20px 30px; background:white;">
      <div style="font-size:15px; font-weight:700; color:#1e3a5f; margin-bottom:4px;">
        Changes Detected ({counts['Total']})
      </div>
      <div style="font-size:12px; color:#64748b; margin-bottom:16px;">
        Please review each change and take necessary action before implementation.
      </div>
      {cards_html}
    </td>
  </tr>
 
  <!-- FOOTER -->
  <tr>
    <td style="padding:14px 30px; background:#f8fafc; border-top:1px solid #e2e8f0;">
      <div style="font-size:11px; color:#94a3b8; text-align:center;">
        This is an automated GxP notification. Please do not reply to this email.<br/>
        Powered by GxP Document Comparison Pipeline — AWS Bedrock Agent + Claude AI
      </div>
    </td>
  </tr>
 
</table>
</td></tr>
</table>
</body>
</html>"""

    return html


# ─────────────────────────────────────────────
# SEND EMAIL
# ─────────────────────────────────────────────
def send_summary_email(
    summary, doc_id, doc_name, doc_version, prev_version, approved_by, approval_date
):

    subject = (
        f"GxP Change Summary: {doc_name or doc_id} | {prev_version} -> {doc_version}"
    )
    attachment_name = f"GxP_Summary_{doc_id}_{prev_version}_vs_{doc_version}.html"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(RECIPIENT_EMAILS)

    html_report = build_html_report(
        summary, doc_id, doc_name, doc_version, prev_version, approved_by, approval_date
    )

    plain_body = (
        f"GxP SOP Change Summary\n"
        f"======================\n"
        f"Document : {doc_name or 'N/A'}\n"
        f"ID       : {doc_id}\n"
        f"Version  : {prev_version} -> {doc_version}\n"
        f"Approved : {approved_by or 'N/A'} on {approval_date or 'N/A'}\n\n"
        f"Please open the attached HTML file for the full formatted report."
    )

    body_part = MIMEMultipart("alternative")
    body_part.attach(MIMEText(plain_body, "plain", "utf-8"))
    body_part.attach(MIMEText(html_report, "html", "utf-8"))
    msg.attach(body_part)

    attachment = MIMEApplication(html_report.encode("utf-8"), Name=attachment_name)
    attachment["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
    attachment["Content-Type"] = "text/html; charset=utf-8"
    msg.attach(attachment)

    ses_client.send_raw_email(
        Source=SENDER_EMAIL,
        Destinations=RECIPIENT_EMAILS,
        RawMessage={"Data": msg.as_string()},
    )
    logger.info(f"HTML email + attachment sent to: {RECIPIENT_EMAILS}")


# ─────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────
def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")
    logger.info(f"apiPath:     {event.get('apiPath')}")
    logger.info(f"httpMethod:  {event.get('httpMethod')}")
    logger.info(f"actionGroup: {event.get('actionGroup')}")

    try:
        # ── FIXED: Parse parameters — requestBody first (OpenAPI), fallback to parameters ──
        param_map = {}
        try:
            props = event["requestBody"]["content"]["application/json"]["properties"]
            param_map = {p["name"]: p["value"] for p in props}
            logger.info("Parsed from requestBody")
        except (KeyError, TypeError):
            parameters = event.get("parameters", [])
            param_map = {p["name"]: p["value"] for p in parameters}
            logger.info("Parsed from parameters")

        summary_s3_path = param_map.get("summary_s3_path")
        summary_json_path = param_map.get("summary_json_path")

        logger.info(f"summary_s3_path:   {summary_s3_path}")
        logger.info(f"summary_json_path: {summary_json_path}")

        if not summary_s3_path or not summary_json_path:
            raise ValueError("'summary_s3_path' and 'summary_json_path' are required.")

        # Read summary text from S3
        summary = read_from_s3(summary_s3_path).decode("utf-8")
        logger.info(f"Summary loaded: {len(summary)} characters")

        # Read metadata from summary.json
        metadata = json.loads(read_from_s3(summary_json_path))
        doc_id = metadata.get("doc_id", "N/A")
        doc_name = metadata.get("doc_name", "N/A")
        doc_version = metadata.get("doc_version", "N/A")
        prev_version = metadata.get("prev_version", "N/A")
        approved_by = metadata.get("approved_by", "N/A")
        approval_date = metadata.get("approval_date", "N/A")

        logger.info(f"Metadata: doc_id={doc_id} {prev_version} -> {doc_version}")

        # Send email
        try:
            if RECIPIENT_EMAILS:
                send_summary_email(
                    summary=summary,
                    doc_id=doc_id,
                    doc_name=doc_name,
                    doc_version=doc_version,
                    prev_version=prev_version,
                    approved_by=approved_by,
                    approval_date=approval_date,
                )
            else:
                logger.warning("No recipient emails configured. Skipping email.")
        except Exception as email_err:
            logger.error(f"Email failed: {email_err!s}")
            import traceback

            traceback.print_exc()

        # ── FIXED: Return OpenAPI format ──
        return build_response(
            event,
            {
                "status": "success",
                "doc_id": doc_id,
                "doc_name": doc_name,
                "txt_path": summary_s3_path,
                "json_path": summary_json_path,
                "email_sent_to": RECIPIENT_EMAILS,
            },
            200,
        )

    except Exception as e:
        logger.error(f"Unexpected error: {e!s}")
        import traceback

        traceback.print_exc()

        # ── FIXED: Return OpenAPI format for errors too ──
        return build_response(event, {"error": str(e)}, 500)
