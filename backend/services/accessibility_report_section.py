"""
The Accessibility section of execution reports (spec 0011, AC-14).

Both report engines use these helpers, so the HTML, PDF, and JUnit outputs say
the same thing:
- `get_accessibility_report_by_execution` loads the run's report (never raises).
- `accessibility_template_context` is the data the professional template's
  `components/accessibility_section.html` renders (its PDF comes from that HTML).
- `accessibility_html_section` and `draw_accessibility_pdf` are the legacy
  engine's HTML block and PDF page.
- `append_accessibility_junit` adds `testsuite name="accessibility"`: one
  testcase per rule that fired, failing for violations; a single passing
  testcase when there are no violations; a single skipped testcase when the
  run was not scanned.

Every output carries DISCLAIMER: an automated scan does not certify WCAG
conformance.
"""

from __future__ import annotations

import html
import logging
import xml.etree.ElementTree as ET
from uuid import UUID

from backend.models.accessibility import AccessibilityReport, FindingKind, GateStatus

logger = logging.getLogger(__name__)

DISCLAIMER = "An automated scan does not certify WCAG conformance."
IMPACTS = ("critical", "serious", "moderate", "minor")
_NOT_SCANNED_TEXT = {
    "disabled": "Accessibility scanning is turned off for this project.",
    "scan_error": "The accessibility scan could not run for this execution. The test result is not affected.",
    "no_report": "This execution has no accessibility report.",
}
_GATE_COLORS = {"PASS": "#10b981", "WARN": "#f59e0b", "BLOCKED": "#ef4444", "NOT_SCANNED": "#94a3b8"}
_PDF_GATE_COLORS = {
    "PASS": (0.06, 0.6, 0.4), "WARN": (0.75, 0.45, 0.02),
    "BLOCKED": (0.8, 0.15, 0.15), "NOT_SCANNED": (0.4, 0.45, 0.5),
}
# Rows in the findings table; the full list is in the app.
MAX_TABLE_ROWS = 50


def get_accessibility_report_by_execution(project_id: UUID, execution_id: UUID, service=None) -> AccessibilityReport | None:
    """The run's accessibility report, or None. A lookup error never breaks a report."""
    try:
        if service is None:
            from backend.services.accessibility_service import AccessibilityService

            service = AccessibilityService()
        return service.report_for_execution(project_id, execution_id)
    except Exception:
        logger.exception("Accessibility report lookup failed: execution_id=%s", execution_id)
        return None


def accessibility_template_context(report: AccessibilityReport | None) -> dict:
    """Plain data for templates and the other renderers. A missing report is
    NOT_SCANNED with reason `no_report`."""
    if report is None:
        return {
            "gate_status": GateStatus.NOT_SCANNED.value, "gate_reason": "no_report", "gate_mode": None,
            "scanned": False, "not_scanned_text": _NOT_SCANNED_TEXT["no_report"], "counts": {},
            "findings": [], "more_findings": 0, "pages": 0, "disclaimer": DISCLAIMER,
            "gate_color": _GATE_COLORS["NOT_SCANNED"],
        }
    status = report.gate_status.value
    scanned = report.gate_status != GateStatus.NOT_SCANNED
    rows = [
        {
            "kind": "Needs review" if f.kind == FindingKind.NEEDS_REVIEW else "Violation",
            "rule_id": f.rule_id,
            "impact": "needs review" if f.kind == FindingKind.NEEDS_REVIEW else (f.impact or "unknown"),
            "wcag": ", ".join(f.wcag_criteria) or "best practice",
            "selector": f.selector,
            "pages": len(f.pages),
            "explanation": f.ai_explanation or f.help,
            "fix": f.ai_fix,
        }
        for f in report.findings
    ]
    return {
        "gate_status": status,
        "gate_reason": report.gate_reason,
        "gate_mode": report.gate_mode.value,
        "scanned": scanned,
        "not_scanned_text": _NOT_SCANNED_TEXT.get(report.gate_reason or "", "This execution was not scanned."),
        "counts": {key: int(report.counts.get(key, 0)) for key in (*IMPACTS, "needs_review")},
        "findings": rows[:MAX_TABLE_ROWS],
        "more_findings": max(0, len(rows) - MAX_TABLE_ROWS),
        "pages": len(report.pages),
        "disclaimer": DISCLAIMER,
        "gate_color": _GATE_COLORS.get(status, "#94a3b8"),
    }


def accessibility_html_section(report: AccessibilityReport | None) -> str:
    """The legacy HTML report's Accessibility section (dark theme, every value escaped)."""
    ctx = accessibility_template_context(report)
    e = lambda value: html.escape("" if value is None else str(value))
    mode = f" <span style='color:#94a3b8;font-size:0.85rem;'>gate mode: {e(ctx['gate_mode'])}</span>" if ctx["gate_mode"] else ""
    header = (
        "<h2>Accessibility</h2>"
        "<div class='summary-card' style='margin-top:12px;'>"
        f"<div style='display:flex;align-items:center;gap:12px;margin-bottom:12px;'>"
        f"<span style='background:{ctx['gate_color']};color:#0f172a;font-weight:700;padding:4px 12px;border-radius:6px;'>"
        f"{e(ctx['gate_status'])}</span>{mode}</div>"
    )
    footer = f"<p style='color:#94a3b8;font-size:0.85rem;margin:12px 0 0;'>{e(ctx['disclaimer'])}</p></div>"
    if not ctx["scanned"]:
        return header + f"<p style='color:#cbd5e1;'>{e(ctx['not_scanned_text'])}</p>" + footer

    counts = " · ".join(
        f"{ctx['counts'][key]} {label}" for key, label in (
            ("critical", "critical"), ("serious", "serious"), ("moderate", "moderate"),
            ("minor", "minor"), ("needs_review", "need review"),
        )
    )
    body = f"<p style='color:#cbd5e1;'>{e(counts)} on {ctx['pages']} page(s).</p>"
    if ctx["findings"]:
        rows = "".join(
            "<tr>"
            f"<td style='padding:6px;border-top:1px solid #334155;'><code>{e(r['rule_id'])}</code></td>"
            f"<td style='padding:6px;border-top:1px solid #334155;'>{e(r['impact'])}</td>"
            f"<td style='padding:6px;border-top:1px solid #334155;'>{e(r['wcag'])}</td>"
            f"<td style='padding:6px;border-top:1px solid #334155;word-break:break-all;'><code>{e(r['selector'])}</code></td>"
            f"<td style='padding:6px;border-top:1px solid #334155;'>{e(r['explanation'])}"
            + (f"<br><span style='color:#94a3b8;'>Fix: {e(r['fix'])}</span>" if r["fix"] else "")
            + "</td></tr>"
            for r in ctx["findings"]
        )
        more = (
            f"<p style='color:#94a3b8;font-size:0.85rem;'>{ctx['more_findings']} more finding(s) are listed in the application.</p>"
            if ctx["more_findings"] else ""
        )
        body += (
            "<table style='width:100%;border-collapse:collapse;color:#cbd5e1;font-size:0.85rem;'>"
            "<caption style='text-align:left;color:#94a3b8;padding-bottom:6px;'>Accessibility findings</caption>"
            "<thead><tr>"
            + "".join(f"<th scope='col' style='text-align:left;padding:6px;'>{h}</th>" for h in ("Rule", "Impact", "WCAG", "Element", "Explanation"))
            + f"</tr></thead><tbody>{rows}</tbody></table>{more}"
        )
    else:
        body += "<p style='color:#cbd5e1;'>No accessibility findings on the scanned pages.</p>"
    return header + body + footer


def draw_accessibility_pdf(add_page, report: AccessibilityReport | None) -> None:
    """Adds the legacy PDF's Accessibility page(s). `add_page(title)` returns a new
    PyMuPDF page with the report's header and footer bars."""
    import fitz

    ctx = accessibility_template_context(report)
    page = add_page("Accessibility")
    page.insert_text(fitz.Point(30, 90), "Accessibility", fontsize=18, color=(0.1, 0.2, 0.4), fontname="helvetica-bold")
    page.draw_line(fitz.Point(30, 100), fitz.Point(565, 100), color=(0.2, 0.5, 0.9), width=2)
    color = _PDF_GATE_COLORS.get(ctx["gate_status"], (0.4, 0.45, 0.5))
    page.draw_rect(fitz.Rect(30, 115, 170, 145), color=color, fill=color, width=0)
    page.insert_text(fitz.Point(40, 135), f"GATE: {ctx['gate_status']}", fontsize=10, color=(1, 1, 1), fontname="helvetica-bold")
    if ctx["gate_mode"]:
        page.insert_text(fitz.Point(185, 135), f"Gate mode: {ctx['gate_mode']}", fontsize=9, color=(0.3, 0.3, 0.3), fontname="helvetica")
    y = 170
    if not ctx["scanned"]:
        page.insert_textbox(fitz.Rect(30, y, 565, y + 40), ctx["not_scanned_text"], fontsize=9, fontname="helvetica", color=(0.2, 0.2, 0.2))
        y += 50
    else:
        c = ctx["counts"]
        summary = (
            f"{c['critical']} critical, {c['serious']} serious, {c['moderate']} moderate, {c['minor']} minor, "
            f"{c['needs_review']} need review, on {ctx['pages']} page(s)."
        )
        page.insert_text(fitz.Point(30, y), summary, fontsize=9, color=(0.2, 0.2, 0.2), fontname="helvetica")
        y += 20
        if not ctx["findings"]:
            page.insert_text(fitz.Point(30, y), "No accessibility findings on the scanned pages.", fontsize=9, color=(0.2, 0.2, 0.2), fontname="helvetica")
            y += 20
        for row in ctx["findings"]:
            if y + 60 > 790:
                page = add_page("Accessibility")
                y = 70
            page.draw_rect(fitz.Rect(30, y, 565, y + 16), color=(0.95, 0.96, 0.98), fill=(0.95, 0.96, 0.98))
            page.insert_text(
                fitz.Point(36, y + 11),
                f"{row['rule_id']}  ·  {row['impact']}  ·  WCAG {row['wcag']}  ·  {row['pages']} page(s)"[:110],
                fontsize=8, color=(0.1, 0.2, 0.4), fontname="helvetica-bold",
            )
            text = f"Element: {row['selector']}\n{row['explanation']}"
            if row["fix"]:
                text += f"\nFix: {row['fix']}"
            page.insert_textbox(fitz.Rect(36, y + 19, 559, y + 58), text[:600], fontsize=7.5, fontname="helvetica", color=(0.15, 0.15, 0.15))
            y += 64
        if ctx["more_findings"]:
            page.insert_text(fitz.Point(30, y), f"{ctx['more_findings']} more finding(s) are listed in the application.", fontsize=8, color=(0.4, 0.4, 0.4), fontname="helvetica")
            y += 16
    if y + 20 > 800:
        page = add_page("Accessibility")
        y = 70
    page.insert_text(fitz.Point(30, y + 8), DISCLAIMER, fontsize=8, color=(0.4, 0.4, 0.4), fontname="helvetica-oblique")


def append_accessibility_junit(parent: ET.Element, report: AccessibilityReport | None) -> ET.Element:
    """Adds `testsuite name="accessibility"` under a `testsuites` element (AC-14)."""
    suite = ET.SubElement(parent, "testsuite", name="accessibility")
    ET.SubElement(ET.SubElement(suite, "properties"), "property", name="disclaimer", value=DISCLAIMER)
    tests = failures = skipped = 0

    if report is None or report.gate_status == GateStatus.NOT_SCANNED:
        reason = (report.gate_reason if report else None) or "no_report"
        case = ET.SubElement(suite, "testcase", name="accessibility scan", classname="accessibility")
        ET.SubElement(case, "skipped", message=f"Not scanned: {reason}")
        tests, skipped = 1, 1
    else:
        rules: dict[str, dict] = {}
        for finding in report.findings:
            entry = rules.setdefault(finding.rule_id, {"violation": False, "impact": finding.impact, "nodes": 0, "help": finding.help, "wcag": set()})
            entry["violation"] |= finding.kind == FindingKind.VIOLATION
            entry["nodes"] += max(1, finding.occurrences)
            entry["wcag"].update(finding.wcag_criteria)
        violations = {rule: data for rule, data in rules.items() if data["violation"]}
        if not violations:
            ET.SubElement(suite, "testcase", name="no accessibility violations", classname="accessibility")
            tests = 1
        for rule, data in sorted(violations.items()):
            case = ET.SubElement(suite, "testcase", name=rule, classname="accessibility")
            wcag = ", ".join(sorted(data["wcag"])) or "best practice"
            failure = ET.SubElement(case, "failure", message=f"{data['impact'] or 'unknown'}: {data['help']}"[:300], type="accessibility")
            failure.text = f"{data['nodes']} element(s). WCAG: {wcag}. {DISCLAIMER}"
            tests += 1
            failures += 1
    suite.set("tests", str(tests))
    suite.set("failures", str(failures))
    suite.set("errors", "0")
    suite.set("skipped", str(skipped))
    return suite
