"""Conservative text extraction for clinician-reviewed order suggestions."""

import re
from typing import Any

_COMMON_MEDICATIONS = [
    "paracetamol", "acetaminophen", "ibuprofen", "aspirin", "atorvastatin",
    "amlodipine", "lisinopril", "metformin", "omeprazole", "esomeprazole",
    "metoprolol", "losartan", "warfarin", "insulin", "prednisone",
    "azithromycin", "amoxicillin", "ciprofloxacin", "ranitidine",
    "diazepam", "loratadine", "cetirizine", "montelukast", "albuterol",
    "salbutamol", "digoxin", "furosemide", "hydrochlorothiazide",
]

_LAB_TEST_PATTERNS = [
    "complete blood count", "blood test", "blood work", "lipid panel",
    "urinalysis", "urine test", "electrocardiogram", "x-ray", "ct scan",
    "creatinine", "prothrombin", "hematocrit", "hemoglobin", "potassium",
    "cholesterol", "glucose", "blood sugar", "thyroid", "liver function",
    "kidney function", "sodium", "hba1c", "cbc", "mri", "ecg", "ekg", "tsh",
    "inr",
]

_MEDICATION_ACTION = re.compile(
    r"\b(?:prescribe|prescribed|start|starting|take|taking|continue|"
    r"continuing|begin|beginning|recommend|administer|give|order)\b",
    re.IGNORECASE,
)
_LAB_ACTION = re.compile(
    r"\b(?:order|ordered|obtain|check|draw|repeat|schedule|send|test)\b",
    re.IGNORECASE,
)
_NEGATED_ORDER = re.compile(
    r"\b(?:do not|don't|does not|doesn't|not|never|stop|stopped|"
    r"discontinue|avoid|allergic to|denies|no need to|not indicated|"
    r"not recommended|consider stopping)\b",
    re.IGNORECASE,
)


def _extract_medication_from_text(text: str) -> dict[str, str] | None:
    if not _MEDICATION_ACTION.search(text) or _NEGATED_ORDER.search(text):
        return None

    medication = None
    lowered = text.lower()
    for candidate in _COMMON_MEDICATIONS:
        if re.search(rf"\b{re.escape(candidate)}\b", lowered):
            medication = candidate
            break

    if not medication:
        match = re.search(
            r"\b([A-Z][a-z]{3,})\s+(\d+(?:\.\d+)?\s*(?:mg|g|ml|mcg|units?))\b",
            text,
        )
        if match:
            medication = match.group(1).lower()
        else:
            return None

    dosage_match = re.search(
        r"\b(\d+(?:\.\d+)?\s*(?:mg|g|ml|mcg|units?))\b",
        text,
        re.IGNORECASE,
    )
    frequency_match = re.search(
        r"\b(?:every\s+\d+\s+hours?|once\s+daily|twice\s+daily|"
        r"three times\s+daily|four times\s+daily|daily|bd|tds|qid|qd|"
        r"prn|as\s+needed)\b",
        text,
        re.IGNORECASE,
    )
    duration_match = re.search(
        r"\bfor\s+\d+\s+(?:days?|weeks?|months?|years?)\b",
        text,
        re.IGNORECASE,
    )
    return {
        "medication": medication,
        "dosage": dosage_match.group(1) if dosage_match else "",
        "frequency": frequency_match.group(0) if frequency_match else "",
        "duration": duration_match.group(0) if duration_match else "",
        "instructions": text,
    }


def extract_medications_from_plan(plan_items: list[str]) -> list[dict[str, str]]:
    suggestions = []
    for item in plan_items:
        suggestion = _extract_medication_from_text(item)
        if suggestion:
            suggestions.append(suggestion)
    return suggestions


def extract_lab_tests_from_soap(
    objective_items: list[str],
    plan_items: list[str],
) -> list[dict[str, str]]:
    suggestions = []
    seen: set[str] = set()
    for item in [*objective_items, *plan_items]:
        if not _LAB_ACTION.search(item) or _NEGATED_ORDER.search(item):
            continue
        lowered = item.lower()
        for pattern in _LAB_TEST_PATTERNS:
            if re.search(rf"(?<!\w){re.escape(pattern)}(?!\w)", lowered) and pattern not in seen:
                seen.add(pattern)
                suggestions.append({
                    "test_name": pattern.title(),
                    "test_code": "",
                    "specimen": "",
                    "source_text": item,
                })
                break
    return suggestions


def preview_orders(note: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    return {
        "medications": extract_medications_from_plan(note.get("plan", [])),
        "laboratory_tests": extract_lab_tests_from_soap(
            note.get("objective", []),
            note.get("plan", []),
        ),
    }
