"""
Generates a clean, printable PDF of a SOAP note — the "Export PDF" button's backend.
Uses fpdf2 (pure Python, no system dependencies like wkhtmltopdf needed).
"""

from fpdf import FPDF
from datetime import datetime


def _safe(text: str) -> str:
    """Base Helvetica only supports latin-1. Real transcripts can contain smart quotes,
    em-dashes, accented names, etc. — degrade gracefully instead of crashing export."""
    if not text:
        return ""
    return text.encode("latin-1", "replace").decode("latin-1")


SECTION_TITLES = {
    "subjective": "SUBJECTIVE",
    "objective": "OBJECTIVE",
    "assessment": "ASSESSMENT",
    "plan": "PLAN",
}


class SoapPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(15, 107, 92)  # clinical teal, matches frontend brand color
        self.cell(0, 10, "ClinicalScribe AI", ln=True)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, "AI-generated documentation draft - clinician review required", ln=True)
        self.ln(4)
        self.set_draw_color(220, 220, 220)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()} - Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", align="C")


def generate_pdf(note: dict, patient_info: dict = None) -> bytes:
    patient_info = patient_info or {}
    pdf = SoapPDF()
    pdf.add_page()

    # Patient info block
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 7, "Patient Information", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(60, 60, 60)
    info_line = f"Name: {_safe(patient_info.get('name') or 'N/A')}    " \
                f"Age: {_safe(patient_info.get('age') or 'N/A')}    " \
                f"Gender: {_safe(patient_info.get('gender') or 'N/A')}"
    pdf.cell(0, 6, info_line, ln=True)
    if patient_info.get("chief_complaint"):
        pdf.cell(0, 6, f"Chief complaint: {_safe(patient_info['chief_complaint'])}", ln=True)
    pdf.cell(0, 6, f"Date: {datetime.now().strftime('%d %B %Y')}", ln=True)
    pdf.ln(6)

    # SOAP sections
    for key in ["subjective", "objective", "assessment", "plan"]:
        items = note.get(key, [])
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(15, 107, 92)
        pdf.cell(0, 8, SECTION_TITLES[key], ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(40, 40, 40)

        if not items:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(150, 150, 150)
            pdf.cell(0, 6, "Not documented", ln=True)
        else:
            for item in items:
                pdf.set_x(14)
                pdf.multi_cell(0, 6, f"-  {_safe(item)}")
        pdf.ln(3)

    # Completeness / review flags, if present
    issues = note.get("issues", [])
    if issues:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(201, 123, 46)  # amber, matches "needs review" styling in the UI
        pdf.cell(0, 7, "Suggested Review Items", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(80, 80, 80)
        for issue in issues:
            pdf.set_x(14)
            pdf.multi_cell(0, 6, f"-  {_safe(issue)}")

    return bytes(pdf.output())
