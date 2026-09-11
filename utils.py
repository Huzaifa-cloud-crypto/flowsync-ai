"""
FlowSync AI - Core Backend Logic (utils.py)
Intelligent Document Triage & Workflow Automation Engine
Powered by Google Gemini API (google-genai SDK)
"""

import io
import json
import os
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from pypdf import PdfReader

# Load environment variables (e.g. GEMINI_API_KEY from .env)
load_dotenv()

# ==========================================
# 1. Pydantic Structured Output Schema
# ==========================================

class ExtractedEntities(BaseModel):
    """Specific entity items parsed from document text."""
    people: List[str] = Field(
        default_factory=list,
        description="Key individuals, customers, authors, or mentioned personnel"
    )
    organizations: List[str] = Field(
        default_factory=list,
        description="Company names, institutions, vendors, or internal departments"
    )
    dates_or_deadlines: List[str] = Field(
        default_factory=list,
        description="Explicit deadlines, SLA timeframes, or incident timestamps"
    )
    identifiers: List[str] = Field(
        default_factory=list,
        description="Account numbers, ticket IDs, invoice IDs, CVEs, or transaction codes"
    )
    financials: List[str] = Field(
        default_factory=list,
        description="Monetary figures, invoice sums, billing amounts, or refund claims"
    )


class TriageResult(BaseModel):
    """Complete structured output for automated document triage."""
    category: str = Field(
        ...,
        description="Primary business intent: e.g. Billing & Finance, Technical Incident, Customer Retention, Legal & Compliance, Academic Appeal, General Operations"
    )
    priority: str = Field(
        ...,
        description="Strict priority tier: High, Medium, or Low"
    )
    priority_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Urgency confidence index from 0 (lowest) to 100 (critical immediate escalation)"
    )
    priority_rationale: str = Field(
        ...,
        description="Clear 1-2 sentence justification explaining why this priority was assigned"
    )
    sentiment: str = Field(
        ...,
        description="Customer or author emotional tone: e.g. Urgent/Frustrated, Inquisitive, Threatening, Neutral, Polite"
    )
    summary: str = Field(
        ...,
        description="A clear, actionable 2-sentence executive summary of the document"
    )
    key_entities: ExtractedEntities = Field(
        default_factory=ExtractedEntities,
        description="Structured entities extracted from the text"
    )
    suggested_action: str = Field(
        ...,
        description="Concrete, actionable operational next step for the resolving team"
    )
    automated_routing: str = Field(
        ...,
        description="Target department or queue (e.g., SRE Escalations, Tier 2 Billing, Legal Counsel, Academic Affairs)"
    )
    workflow_trigger: str = Field(
        ...,
        description="Recommended automation hook (e.g., PagerDuty P1, Zendesk High-SLA Ticket, Stripe Ledger Hold, Slack #ops-urgent)"
    )


# ==========================================
# 2. System Instructions
# ==========================================

TRIAGE_SYSTEM_INSTRUCTION = """
You are FlowSync AI, an enterprise-grade document triage and workflow automation intelligence agent.
Your mission is to instantly parse incoming unstructured documents (emails, support tickets, incident reports, university academic appeals, customer disputes, and invoice discrepancy notices).

You must strictly evaluate:
1. Primary Category (Billing & Finance, Technical Incident, Customer Retention, Legal & Compliance, Academic Appeal, General Operations).
2. Priority (High, Medium, Low):
   - 'High': System outages, security breaches, legal action threats, regulatory non-compliance, active customer churn threats with high ACV, urgent SLA deadlines (<24 hours), or financial errors exceeding $1,000.
   - 'Medium': Standard billing inquiries, degraded performance with workaround, grading policy questions, moderate contract disputes, or routine operational requests with standard 3-5 day SLA.
   - 'Low': General feedback, documentation clarification, archives, greetings, non-critical general queries.
3. Priority Score (0-100 integer confidence metric reflecting urgency and impact).
4. Concise Executive Summary (max 2 sentences).
5. Entity Extraction (People, Organizations, Dates/Deadlines, Identifiers, Financial amounts).
6. Operational Next Action & Automated Routing destination.
7. Automated Workflow Trigger (e.g. Zendesk, PagerDuty, Slack, ERP webhook).

Always return valid, well-formed JSON matching the required schema. Never hallucinate entities that are not present or strongly implied in the text.
"""


# ==========================================
# 3. Document Extraction Helpers
# ==========================================

def extract_text_from_file(uploaded_file) -> str:
    """
    Extracts raw text content from an uploaded file object.
    Supports .txt and .pdf files.
    """
    if uploaded_file is None:
        return ""

    filename = getattr(uploaded_file, "name", "").lower()

    if filename.endswith(".pdf"):
        try:
            reader = PdfReader(uploaded_file)
            extracted_pages = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    extracted_pages.append(page_text.strip())
            return "\n\n".join(extracted_pages)
        except Exception as e:
            raise ValueError(f"Failed to extract text from PDF: {str(e)}")

    elif filename.endswith(".txt") or filename.endswith(".log") or filename.endswith(".md"):
        try:
            content = uploaded_file.read()
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")
            return str(content)
        except Exception as e:
            raise ValueError(f"Failed to read text file: {str(e)}")

    else:
        # Fallback raw byte reading
        try:
            content = uploaded_file.read()
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")
            return str(content)
        except Exception as e:
            raise ValueError(f"Unsupported file format: {filename}. Error: {str(e)}")


# ==========================================
# 4. Core Gemini AI Triage Invocation
# ==========================================

def analyze_document_with_gemini(
    document_text: str,
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash"
) -> Dict[str, Any]:
    """
    Executes deep document triage using the official google-genai SDK.
    Enforces strict Pydantic JSON schema output and provides resilient fallback handling.
    """
    clean_text = document_text.strip()
    if not clean_text:
        return {
            "success": False,
            "error": "Document text is empty. Please provide text or an upload with readable content."
        }

    # Resolve API Key from argument or environment
    effective_api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not effective_api_key:
        # Graceful fallback heuristic mode for zero-configuration preview
        return run_heuristic_triage(clean_text, notice="Simulated via Local Heuristic Fallback (GEMINI_API_KEY not detected).")

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=effective_api_key)

        prompt = f"Analyze and triage the following document content:\n\n---\n{clean_text}\n---"

        # Call Gemini with structured schema output
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=TRIAGE_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=TriageResult,
                temperature=0.1,  # Low temperature for deterministic classification
            ),
        )

        response_text = response.text
        if not response_text:
            raise ValueError("Gemini API returned an empty response.")

        # Parse JSON and validate with Pydantic
        parsed_json = json.loads(response_text)
        validated_result = TriageResult.model_validate(parsed_json)

        return {
            "success": True,
            "data": validated_result.model_dump(),
            "raw_json": response_text,
            "source": "Gemini API (Production)",
            "model": model_name
        }

    except ImportError:
        # If google-genai isn't installed in the runtime, attempt fallback to google.generativeai or heuristics
        return fallback_with_legacy_or_heuristics(clean_text, effective_api_key)

    except Exception as e:
        error_msg = str(e)
        # Check for quota or key errors and provide helpful debug info
        if "403" in error_msg or "API_KEY_INVALID" in error_msg:
            err_detail = "Invalid Gemini API Key. Please verify your credentials in Settings > Secrets."
        elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            err_detail = "Gemini API rate limit or quota exceeded. Falling back to heuristic classifier."
        else:
            err_detail = f"Gemini API invocation error: {error_msg}"

        fallback_result = run_heuristic_triage(clean_text, notice=f"Fallback triggered: {err_detail}")
        fallback_result["api_error"] = error_msg
        return fallback_result


def fallback_with_legacy_or_heuristics(text: str, api_key: str) -> Dict[str, Any]:
    """Fallback handler when google-genai is not in current environment."""
    try:
        import google.generativeai as legacy_genai
        legacy_genai.configure(api_key=api_key)
        model = legacy_genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=TRIAGE_SYSTEM_INSTRUCTION
        )
        prompt = f"Return strictly a JSON object matching the TriageResult schema for:\n\n{text}"
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        parsed = json.loads(response.text)
        validated = TriageResult.model_validate(parsed)
        return {
            "success": True,
            "data": validated.model_dump(),
            "source": "Google GenerativeAI (Legacy Fallback)",
        }
    except Exception as e:
        return run_heuristic_triage(text, notice=f"Heuristic mode activated due to: {str(e)}")


def run_heuristic_triage(text: str, notice: str = "") -> Dict[str, Any]:
    """
    Deterministic rule-based heuristic triage engine.
    Guarantees that demo workflows, tests, and CI/CD never break even if offline.
    """
    lower = text.lower()

    # Determine intent & category
    if any(k in lower for k in ["invoice", "payment", "billing", "refund", "credit card", "chargeback", "receipt", "overdue"]):
        category = "Billing & Finance"
        is_high = any(k in lower for k in ["fraud", "overdue", "unauthorized", "legal", "$10", "$50", "$100", "immediately"])
        priority = "High" if is_high else "Medium"
        priority_score = 88 if is_high else 55
        routing = "Finance & Accounts Receivable"
        action = "Audit transactional ledger and review invoice dispute line items"
        trigger = "ERP Webhook: Hold Account & Notify Accounting"
    elif any(k in lower for k in ["outage", "down", "crash", "error 500", "cve", "breach", "latency", "ddos", "incident"]):
        category = "Technical Incident"
        priority = "High"
        priority_score = 96
        routing = "Site Reliability Engineering (SRE)"
        action = "Declare Sev-1 bridge and verify status page health"
        trigger = "PagerDuty Alert & Slack #incident-response"
    elif any(k in lower for k in ["cancel", "churn", "terrible", "unacceptable", "competitor", "switch to", "disappointed"]):
        category = "Customer Retention"
        priority = "High"
        priority_score = 84
        routing = "Customer Success Leadership"
        action = "Initiate executive outreach within 2-hour SLA window"
        trigger = "HubSpot/Salesforce Escalation Flag"
    elif any(k in lower for k in ["appeal", "dean", "provost", "syllabus", "gpa", "course", "grade", "faculty", "academic"]):
        category = "Academic Appeal"
        priority = "Medium"
        priority_score = 62
        routing = "Academic Standards & Registrar"
        action = "Collate student docket and assign faculty ombudsperson"
        trigger = "Student Portal Academic Review Ticket"
    elif any(k in lower for k in ["lawyer", "attorney", "subpoena", "gdpr", "compliance", "arbitration", "litigation"]):
        category = "Legal & Compliance"
        priority = "High"
        priority_score = 98
        routing = "Legal Counsel & Compliance"
        action = "Secure document preservation hold and brief general counsel"
        trigger = "Encrypted Legal Vault Dispatch"
    else:
        category = "General Operations"
        priority = "Low"
        priority_score = 30
        routing = "Frontline Operational Triage"
        action = "Route to standard knowledgebase inquiry queue"
        trigger = "Helpdesk Ticket Creation"

    # Entity Regex matchers
    amounts = re.findall(r"\$\s?[0-9,]+(?:\.[0-9]{2})?", text)
    emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    identifiers = re.findall(r"\b(?:INV|TICK|DOC|REF|ACCT|INC)-[0-9A-Z]{3,8}\b", text, re.IGNORECASE)

    extracted_entities = {
        "people": emails if emails else ["Key Sender/Author"],
        "organizations": ["Detected Organization/Account"],
        "dates_or_deadlines": ["24-hour SLA Window"] if priority == "High" else ["Standard 3-5 Business Days"],
        "identifiers": identifiers if identifiers else ["DOC-AUTO-001"],
        "financials": amounts if amounts else [],
    }

    result = {
        "category": category,
        "priority": priority,
        "priority_score": priority_score,
        "priority_rationale": f"Classified as {priority} priority based on operational impact keywords and domain heuristics.",
        "sentiment": "Escalated/Urgent" if priority == "High" else "Neutral",
        "summary": (text[:160].replace("\n", " ") + "...") if len(text) > 160 else text,
        "key_entities": extracted_entities,
        "suggested_action": action,
        "automated_routing": routing,
        "workflow_trigger": trigger,
    }

    return {
        "success": True,
        "data": result,
        "source": "Heuristic Rules Engine (Fallback)",
        "notice": notice
    }
