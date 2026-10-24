"""Finding model and the standards references used in reports.

The references are orientation aids: this is an internal checker, it does not
certify PDF/UA or WCAG conformance.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

ERROR, WARNING, MANUAL, NOT_RUN = "error", "warning", "manual", "not_run"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, MANUAL: 2, NOT_RUN: 3}
KIND = {ERROR: "machine-detectable", WARNING: "heuristic / quality", MANUAL: "manual review", NOT_RUN: "not performed"}

# PDF Accessibility classifications required by PDF/UA & WCAG audits:
CLASS_STRUCTURAL_ERROR = "structural_error"       # Invalid/inconsistent structure, broken references, cycles, ParentTree mismatches
CLASS_ACCESSIBILITY_FAILURE = "accessibility_failure" # Violates accessibility requirements (missing alt text, skipped headings, untagged fields)
CLASS_SEMANTIC_ISSUE = "semantic_issue"           # Tags that may not represent content correctly (suspected layout tables, weak alt text)
CLASS_MANUAL_REVIEW = "manual_review"             # Visual meaning, reading order, contrast, and checks requiring human evaluation
CLASS_NOT_RUN = "not_run"                         # Checks not performed by this tool

VERIF_CONFIRMED = "confirmed"                     # Deterministically verified against PDF object graph
VERIF_SUSPECTED = "suspected"                     # Heuristic/quality analysis
VERIF_MANUAL = "manual_review"                    # Requires human or screen reader evaluation
VERIF_NOT_RUN = "not_run"                         # Check was not executed

_WCAG = "https://www.w3.org/WAI/WCAG21/Understanding/"
_UA = "https://pdfa.org/resource/iso-14289-pdfua/"
_MH = "https://pdfa.org/resource/the-matterhorn-protocol/"


def ua(clause):
    return {"std": "PDF/UA-1 (ISO 14289-1:2014)", "id": "§" + clause, "url": _UA}


def wcag(sc, slug):
    return {"std": "WCAG 2.1", "id": "SC " + sc, "url": _WCAG + slug}


def mh(cp, label):
    return {"std": "Matterhorn Protocol 1.1", "id": f"checkpoint {cp} ({label})", "url": _MH}


def iso32000(clause):
    return {"std": "ISO 32000-1:2008 (PDF 1.7)", "id": "§" + clause, "url": ""}


def iso32000_2(clause):
    return {"std": "ISO 32000-2:2020 (PDF 2.0)", "id": "§" + clause, "url": ""}


_STRUCT_PREFIXES = ("STRUCT-", "ROLE-", "PT-", "MCID-", "PARENT-")


@dataclass
class Finding:
    code: str
    severity: str
    category: str
    title: str
    detail: str = ""
    evidence: list = field(default_factory=list)
    pages: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    recommendation: str = ""
    fix: dict | None = None
    count: int = 1
    source: str = "internal check"
    classification: str = ""
    verification: str = ""
    impact: str = ""
    obj_refs: list = field(default_factory=list)

    def __post_init__(self):
        if not self.classification:
            if self.severity == NOT_RUN:
                self.classification = CLASS_NOT_RUN
            elif self.severity == MANUAL:
                self.classification = CLASS_MANUAL_REVIEW
            elif any(self.code.startswith(p) for p in _STRUCT_PREFIXES):
                self.classification = CLASS_STRUCTURAL_ERROR
            elif self.severity == ERROR:
                self.classification = CLASS_ACCESSIBILITY_FAILURE
            else:
                self.classification = CLASS_SEMANTIC_ISSUE

        if not self.verification:
            if self.severity == NOT_RUN:
                self.verification = VERIF_NOT_RUN
            elif self.severity == MANUAL:
                self.verification = VERIF_MANUAL
            elif self.classification in (CLASS_STRUCTURAL_ERROR, CLASS_ACCESSIBILITY_FAILURE):
                self.verification = VERIF_CONFIRMED
            else:
                self.verification = VERIF_SUSPECTED

    def to_dict(self):
        d = asdict(self)
        d["kind"] = KIND[self.severity]
        d["classification"] = self.classification
        d["verification"] = self.verification
        d["impact"] = self.impact
        d["obj_refs"] = self.obj_refs[:50]
        d["pages"] = sorted(set(self.pages))[:200]
        d["evidence"] = self.evidence[:25]
        d["nodes"] = self.nodes[:200]
        return d
