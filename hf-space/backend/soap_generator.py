"""
Turns a raw doctor-patient conversation transcript into a structured SOAP note:

    S — Subjective : what the patient reports (symptoms, history, concerns, in their own words)
    O — Objective  : what's observed/measured (vitals, exam findings, test results mentioned)
    A — Assessment : the clinical impression / likely diagnosis discussed
    P — Plan       : treatment, medications, follow-up, referrals

Two backends are supported:

1. RULE-BASED (default, always available, no API key needed)
   Uses comprehensive keyword/phrase matching with clinical lexicons, negation-aware
   classification, multi-clause splitting, and speaker-inference heuristics.

2. LLM-ASSISTED (optional, requires an API key)
   Sends the transcript to an LLM with a structured prompt for much higher-quality,
   properly-summarized notes. Falls back to rule-based automatically if no key is set or
   the API call fails, so the app never breaks without an LLM.

IMPORTANT — this is a documentation *drafting* aid, not a diagnostic tool. Every generated
note is meant to be reviewed and edited by the clinician before it goes in a real record.
"""

import os
import re
import json
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Comprehensive keyword banks — designed to handle real, messy clinical data
# ---------------------------------------------------------------------------

# --- SUBJECTIVE: patient-reported symptoms, history, concerns, feelings ---
SUBJECTIVE_SYMPTOM_TERMS = [
    r"\bheadache\b", r"\bmigraine\b", r"\bdizziness\b", r"\bvertigo\b",
    r"\bfatigue\b", r"\btired\b", r"\bexhausted\b", r"\bweakness\b",
    r"\bnausea\b", r"\bnauseous\b", r"\bvomiting\b", r"\bvomit\b", r"\bdiarrhea\b", r"\bconstipation\b",
    r"\bphlegm\b", r"\bmucus\b", r"\bsputum\b",
    r"\bpain\b", r"\bache\b", r"\bdiscomfort\b", r"\bsore\b",
    r"\bfever\b", r"\bchills\b", r"\bsweating\b", r"\bnight sweats\b",
    r"\bcough\b", r"\bsneeze\b", r"\bcongestion\b", r"\brunny nose\b",
    r"\bshortness of breath\b", r"\bsob\b", r"\bwheezing\b", r"\bchest tightness\b",
    r"\bpalpitations\b", r"\bracing heart\b", r"\birregular heartbeat\b",
    r"\babdominal pain\b", r"\bstomach pain\b", r"\bcramp\b", r"\bbloating\b",
    r"\bjoint pain\b", r"\barthritis\b", r"\bswelling\b", r"\binflammation\b",
    r"\bback pain\b", r"\bneck pain\b", r"\bshoulder pain\b", r"\bknee pain\b",
    r"\banxiety\b", r"\bdepression\b", r"\bstress\b", r"\bworried\b",
    r"\binsomnia\b", r"\bsleep\b", r"\bappetite\b", r"\bweight loss\b",
    r"\bweight gain\b", r"\brash\b", r"\bitch\b", r"\bitching\b",
    r"\bvision\b", r"\bblurry\b", r"\bhearing\b", r"\btinnitus\b",
    r"\bsore throat\b", r"\bthroat\b", r"\bhoarse\b", r"\bswallowing\b",
    r"\bbreathless\b", r"\bingestion\b", r"\bindigestion\b", r"\bheartburn\b",
    r"\bgas\b", r"\bblood\b", r"\bstool\b", r"\burine\b",
    r"\bburning\b", r"\bfrequency\b", r"\burgency\b", r"\bincontinence\b",
    r"\bmenstrual\b", r"\bperiod\b", r"\bcramping\b", r"\bspotting\b",
    r"\bnumbness\b", r"\btingling\b", r"\btremor\b", r"\bseizure\b",
    r"\bfainting\b", r"\bfall\b", r"\binjury\b", r"\bwound\b",
    r"\btender\b", r"\bsensitivity\b", r"\bsensitive\b", r"\bburn\b",
    # Body parts — patient-reported symptoms often reference these
    r"\bstomach\b", r"\bhead\b", r"\bchest\b", r"\bthroat\b", r"\bback\b",
    r"\bneck\b", r"\bshoulder\b", r"\bknee\b", r"\belbow\b", r"\bwrist\b",
    r"\bhand\b", r"\bfoot\b", r"\barm\b", r"\bleg\b", r"\bhip\b",
    r"\beye\b", r"\bear\b", r"\bnose\b", r"\bmouth\b", r"\btooth\b",
    r"\bjoint\b", r"\bmuscle\b", r"\bheart\b", r"\blung\b", r"\bkidney\b",
    r"\bliver\b", r"\bintestine\b", r"\bcolon\b", r"\bbladder\b",
    # Common patient-reported conditions (also in assessment, but first-person = subjective)
    r"\bacid reflux\b", r"\bgerd\b", r"\bheartburn\b", r"\bindigestion\b",
    r"\bconstipation\b", r"\bdiarrhea\b", r"\bgas\b", r"\bbloating\b",
    r"\ballerg\b", r"\basthma\b", r"\bdiabetes\b", r"\bhypertension\b",
    r"\bhigh blood pressure\b", r"\bcholesterol\b", r"\bthyroid\b",
]

SUBJECTIVE_PHRASE_MARKERS = [
    r"\bi\b(?:\s+\w+){0,3}\s+feel(?:ing)?\b", r"\bi have been\b", r"\bi've been\b",
    r"\bi am having\b", r"\bi'm having\b", r"\bi have had\b", r"\bi've had\b",
    r"\bi have got\b", r"\bi've got\b", r"\bi am getting\b", r"\bi'm getting\b",
    r"\bi noticed\b", r"\bi've noticed\b", r"\bi notice\b",
    r"\bi am experiencing\b", r"\bi'm experiencing\b", r"\bi experience\b",
    r"\bi think i\b", r"\bi believe i\b", r"\bi feel like\b", r"\bi feel that\b",
    r"\bit hurts\b", r"\bmy \w+ hurts\b", r"\bmy \w+ aches\b", r"\bmy \w+ feels\b",
    r"\bi am worried\b", r"\bi'm worried\b", r"\bi am concerned\b", r"\bi'm concerned\b",
    r"\bi have a\b", r"\bi've had a\b", r"\bi cannot sleep\b", r"\bi can't sleep\b",
    r"\bi have difficulty\b", r"\bi've had trouble\b", r"\bi am bothered by\b",
    r"\bsince\s+\w+", r"\bfor\s+\d+\s+(day|days|week|weeks|month|months|year|years)\b",
    r"\bbegan\b", r"\bstarted\b", r"\bworsen\b", r"\bworsening\b", r"\bimprove\b",
]

# --- OBJECTIVE: vitals, measurements, exam findings, test results ---
OBJECTIVE_VITALS_TERMS = [
    r"\bblood pressure\b", r"\bbp\b", r"\bsystolic\b", r"\bdiastolic\b",
    r"\btemperature\b", r"\btemp\b", r"\bheart rate\b", r"\bhr\b",
    r"\bpulse\b", r"\brespiratory rate\b", r"\brr\b", r"\boxygen saturation\b",
    r"\bspo2\b", r"\bweight\b", r"\bheight\b", r"\bbmi\b",
    r"\bwaist\b", r"\bhead circumference\b", r"\bpeak flow\b",
]

OBJECTIVE_MEASUREMENT_PATTERNS = [
    r"\b\d{2,3}/\d{2,3}\b",                      # 120/80 (BP)
    r"\b\d{2,3}\s?(?:bpm|beats? per minute|beats/min)\b",   # 88 bpm
    r"\b\d{2,3}\.\d\s?°?\s?[FC]\b",              # 100.4 F / 38.5 C
    r"\b\d{2,3}\s?°?\s?[FC]\b",                  # 100 F / 38 C
    r"\b\d{2,3}\s?/\s?\d{2,3}\s?mm\s?hg\b",      # 128/82 mmHg
    r"\b\d{1,3}\s?(?:kg|kg|pounds|lbs|lb|g|mg)\b",  # 70 kg / 154 lbs / 500mg
    r"\b\d{1,3}\.?\d?\s?cm\b",                    # 170 cm
    r"\b\d{1,3}\s?%\b",                          # 98% SpO2
    r"\b\d{1,3}\.?\d?\s?mmol/?l?\b",             # 5.4 mmol/L
    r"\b\d{1,3}\.?\d?\s?mg/?dl?\b",              # 90 mg/dL
    r"\b\d{1,3}\.?\d?\s?units?\b",               # doses
    r"\b\d{1,3}\.?\d?\s?mg/?m?\s?l?\b",          # 0.5 mL
]

OBJECTIVE_EXAM_TERMS = [
    r"\bexam\b", r"\bon examination\b", r"\bphysical exam\b",
    r"\bauscultation\b", r"\bpalpation\b", r"\bpercussion\b",
    r"\blungs\b", r"\blung sounds\b", r"\bbreath sounds\b",
    r"\bheart sounds\b", r"\bmurmur\b", r"\bgrade\b",
    r"\babdomen\b", r"\babdominal exam\b", r"\bbowel sounds\b",
    r"\bpupils\b", r"\beye exam\b", r"\bfundoscopy\b",
    r"\bears\b", r"\bnose\b", r"\bthroat\b", r"\bpharynx\b",
    r"\btonsils\b", r"\blymph nodes\b", r"\badenopathy\b",
    r"\bskin\b", r"\bappears\b", r"\bappearance\b",
    r"\bclear\b", r"\bcrackles\b", r"\bwheezes\b", r"\brhonchi\b",
    r"\btenderness\b", r"\bguarding\b", r"\bdistension\b",
    r"\bedema\b", r"\bpitting\b", r"\bjoint exam\b", r"\brom\b",
    r"\bneurological\b", r"\bneuro\b", r"\breflexes\b",
    r"\bgait\b", r"\bcoordination\b", r"\bsensation\b", r"\bstrength\b",
]

OBJECTIVE_TEST_TERMS = [
    r"\bx-ray\b", r"\bxray\b", r"\bct scan\b", r"\bmri\b", r"\bultrasound\b",
    r"\blab results\b", r"\bblood test\b", r"\bblood work\b", r"\bcbc\b",
    r"\bbmp\b", r"\bchemistry\b", r"\bglucose\b", r"\bhemoglobin\b",
    r"\bhba1c\b", r"\bcholesterol\b", r"\bldl\b", r"\bhdl\b",
    r"\btriglycerides\b", r"\bpotassium\b", r"\bsodium\b", r"\bcreatinine\b",
    r"\bbun\b", r"\bgfr\b", r"\blft\b", r"\bast\b", r"\balt\b",
    r"\bbilirubin\b", r"\btsh\b", r"\bthyroid\b", r"\buti\b",
    r"\burinalysis\b", r"\bua\b", r"\bculture\b", r"\bsensitivity\b",
    r"\becg\b", r"\bekg\b", r"\becho\b", r"\bstress test\b",
    r"\bpft\b", r"\bspirometry\b", r"\bfinal\b",
]

# --- ASSESSMENT: clinical impression, likely diagnosis, differential ---
ASSESSMENT_DIAGNOSIS_TERMS = [
    r"\bdiagnosis\b", r"\bdiagnos", r"\bdifferential\b", r"\bdx\b",
    r"\bviral infection\b", r"\bbacterial infection\b", r"\binfection\b",
    r"\bflu\b", r"\binfluenza\b", r"\bcold\b", r"\bpneumonia\b",
    r"\bbronchitis\b", r"\basthma\b", r"\bcopd\b", r"\bsinusitis\b",
    r"\bpharyngitis\b", r"\btonsillitis\b", r"\bottitis\b",
    r"\bgastroenteritis\b", r"\bfood poisoning\b", r"\bappendicitis\b",
    r"\bugi\b", r"\buti\b", r"\bkidney\b", r"\bstones\b",
    r"\bhypertension\b", r"\bhypotension\b", r"\bhigh blood pressure\b",
    r"\bdiabetes\b", r"\btype 2\b", r"\btype 1\b", r"\bprediabetes\b",
    r"\bcholesterol\b", r"\bhyperlipidemia\b", r"\bdyslipidemia\b",
    r"\bmigraine\b", r"\btension headache\b", r"\bcluster headache\b",
    r"\banemia\b", r"\biron deficiency\b", r"\bdehydration\b",
    r"\bacid reflux\b", r"\bgerd\b", r"\bulcer\b", r"\bgastritis\b",
    r"\bars\b", r"\bosteoarthritis\b", r"\brheumatoid\b", r"\bgout\b",
    r"\bfibro\b", r"\bcarpal\b", r"\btendonitis\b", r"\bbursitis\b",
    r"\bsprain\b", r"\bstrain\b", r"\bfracture\b", r"\bdislocat",
    r"\banxiety\b", r"\bsituational\b", r"\bmajor depressive\b", r"\bdepression\b",
    r"\binsomnia\b", r"\bsleep apnea\b", r"\bosa\b",
    r"\balcoholic\b", r"\bwithdrawal\b", r"\bintoxication\b",
    r"\ballergy\b", r"\ballergic rhinitis\b", r"\banaphyl\b",
    r"\bceliac\b", r"\bibd\b", r"\bibs\b", r"\bcrohn\b", r"\bcolitis\b",
    r"\bhypothyroid\b", r"\bhyperthyroid\b", r"\bgrave\b", r"\bhashimoto\b",
    r"\bsepsis\b", r"\bshock\b", r"\bhypovolemic\b",
    r"\batrial\b", r"\bflutter\b", r"\bbrady\b", r"\btachy\b",
    r"\bcardio\b", r"\bmi\b", r"\bstemi\b", r"\bnstemi\b", r"\bangina\b",
    r"\bcva\b", r"\bstroke\b", r"\btia\b",
]

ASSESSMENT_PHRASE_MARKERS = [
    r"\bit looks like\b", r"\bit appears\b", r"\bthis appears to be\b",
    r"\bconsistent with\b", r"\bcompatible with\b", r"\bsuggestive of\b",
    r"\bmy impression\b", r"\bimpression\b", r"\bassessment\b",
    r"\blikely\b", r"\bmost likely\b", r"\bprobably\b", r"\bpossibly\b",
    r"\bsuspect\b", r"\bsuspicious\b", r"\bthink it.{0,5}(is|might be|could be)\b",
    r"\bmight be\b", r"\bcould be\b", r"\bseems to be\b", r"\bappears to be\b",
    r"\brule out\b", r"\br/o\b", r"\bdifferential\b", r"\bprimary\b",
    r"\bsecondary\b", r"\bworking diagnosis\b", r"\bprovisional\b",
    r"\bdiagnosis is\b", r"\bdiagnosis of\b", r"\bwe are seeing\b",
    r"\bwe have\b", r"\bit is\b", r"\bit appears\b",
    r"\bbased on\b", r"\bfrom what\b", r"\bwhat you.{0,10}(describe|say|tell)\b",
]

# --- PLAN: treatment, medication, follow-up, referrals ---
PLAN_MEDICATION_TERMS = [
    r"\bmedication\b", r"\bmeds\b", r"\bprescription\b", r"\bprescribe\b",
    r"\bparacetamol\b", r"\bacetaminophen\b", r"\btylenol\b",
    r"\bibuprofen\b", r"\bmotrin\b", r"\badvil\b", r"\bnaproxen\b",
    r"\baleve\b", r"\baspirin\b", r"\bamoxicillin\b", r"\bpenicillin\b",
    r"\bazithromycin\b", r"\bzithromax\b", r"\bmetformin\b",
    r"\blisinopril\b", r"\bamlodipine\b", r"\batorvastatin\b",
    r"\bsimvastatin\b", r"\bomeprazole\b", r"\bprilosec\b", r"\bnexium\b",
    r"\besomeprazole\b", r"\branitidine\b", r"\bsalbutamol\b",
    r"\bventolin\b", r"\bsteroid\b", r"\bprednisone\b", r"\bhydrocortisone\b",
    r"\bantihistamine\b", r"\bcetirizine\b", r"\bzyrtec\b", r"\bloratadine\b",
    r"\bclaritin\b", r"\bdiphenhydramine\b", r"\bbenadryl\b",
    r"\binsulin\b", r"\bglipizide\b", r"\bmethocarbamol\b", r"\bmuscle relaxant\b",
    r"\bvitamin\b", r"\bsupplement\b", r"\bprobiotic\b", r"\bantibiotic\b",
    r"\bantiviral\b", r"\bantifungal\b", r"\bpain reliever\b", r"\banalgesic\b",
]

PLAN_ACTION_TERMS = [
    r"\btake\b", r"\bstart\b", r"\bbegin\b", r"\bincrease\b", r"\bdecrease\b",
    r"\bstop\b", r"\bdiscontinue\b", r"\bcontinue\b", r"\bmaintain\b",
    r"\breduce\b", r"\badjust\b", r"\bchange\b", r"\bswitch\b",
    r"\bapply\b", r"\buse\b", r"\binject\b", r"\btake orally\b",
    r"\bdrink\b", r"\beat\b", r"\bavoid\b", r"\brest\b", r"\bhydrate\b",
    r"\bdrink plenty\b", r"\bdrink water\b", r"\brest\b", r"\bsleep\b",
]

PLAN_FOLLOWUP_TERMS = [
    r"\bfollow up\b", r"\bfollow-up\b", r"\bcome back\b", r"\breturn\b",
    r"\bschedule\b", r"\bappointment\b", r"\bcheck up\b", r"\bcheckup\b",
    r"\bsee\s+me\b", r"\bsee\s+you\b", r"\bcome in\b", r"\bvisit\b",
    r"\breevaluate\b", r"\breview\b", r"\bmonitor\b", r"\bwatch\b",
    r"\brefer\b", r"\breferral\b", r"\bspecialist\b", r"\bconsult\b",
    r"\bweeks?\b", r"\bmonths?\b", r"\bin\s+\d+\s+(day|days|week|weeks)\b",
    r"\bemergency\b", r"\ber\b", r"\b911\b", r"\bimmediately\b",
    r"\bif\s+(?:the|it|you)\s+(?:fever|pain|worsen|doesn't|does not|gets)\b",
    r"\bworse\b", r"\bhigh\s+fever\b", r"\babove\b",
]

PLAN_DOSE_PATTERN = [
    r"\b\d+\s?(?:mg|mcg|g|ml|cc|tablets?|tabs?|caps?|drops?|puffs?|units?)\b",
    r"\bevery\s+\d+\s*(?:hour|hours|hrs|day|days)\b",
    r"\b(?:once|twice|three times)\s+a\s+day\b",
    r"\b\d+\s*(?:times)?\s*a\s+(?:day|week|month)\b",
    r"\bp\.?o\.?\b", r"\bq\.?\s?[0-9]+h\b", r"\bqd\b", r"\bbid\b", r"\btid\b", r"\bqid\b",
    r"\bprn\b", r"\btake\s+\d\b", r"\b\d+\s+mg\b",
]

# --- Negation words — used to exclude lines that describe *absent* findings ---
NEGATION_PATTERNS = [
    r"\bno\s+(fever|pain|nausea|cough|headache|symptom|symptoms|difficulty|problem|allergy|allergies|swelling|rash|dizziness|bleeding|blood|infection)\b",
    r"\bno\s+(?:known|previous|history\s+of)\s+(?:allerg|reaction|condition|medical)\b",
    r"\bdid not\s+(?:have|experience|notice|feel)\b",
    r"\bdidn't\s+(?:have|experience|notice|feel)\b",
    r"\bdo not\s+(?:have|experience|notice|feel)\b",
    r"\bdon't\s+(?:have|experience|notice|feel)\b",
    r"\bdoes not\s+(?:have|experience|notice|feel)\b",
    r"\bdoesn't\s+(?:have|experience|notice|feel)\b",
    r"\bhavent?\s+had\b",
    r"\bwithout\s+(?:any|the|a)\s+\w+\b",
    r"\bdeny\w*\s+\w*\s+\b",
    r"\breports?\s+no\b",
    r"\bnot\s+(?:feeling|have|having|experiencing|noticing|suffering)\b",
    r"\bnever\s+(?:had|felt|experienced)\b",
    r"\babsence\s+of\b",
]

# --- Speaker inference — lines without explicit labels ---
QUESTION_MARKERS = [
    r"\?$", r"\bwhat\b", r"\bhow\b", r"\bwhose\b", r"\bcan you\b",
    r"\bcould you\b", r"\bwould you\b", r"\bdo you\b", r"\bdid you\b",
    r"\bhave you\b", r"\bhas it\b", r"\bare you\b", r"\bis it\b",
    r"\bdoes it\b", r"\bany\b", r"\btell me\b", r"\bdescribe\b",
    r"\blet me\b", r"\bli{2}\b", r"\bbelieve\b",
]

PATIENT_TELLTALE_MARKERS = [
    r"\bi feel\b", r"\bmy\b", r"\bi have\b", r"\bi've been\b", r"\bme\b",
    r"\bi am\b", r"\bi'm\b", r"\bi think\b", r"\bi've had\b", r"\bi notice",
]

DOCTOR_TELLTALE_MARKERS = [
    r"\byour\b", r"\byou\b", r"\blet me\b", r"\bsir\b", r"\bma'am\b", r"\bmadam\b",
    r"\bplease\b", r"\bdont worry\b", r"\bdon't worry\b",
    r"\bpull up\b", r"\bfill this\b", r"\bsign\b", r"\bprescription\b",
    r"\bgonna\b", r"\bgoing to\b", r"\bwill\b", r"\bshould\b",
]

DOCTOR_TURN_MARKERS = [r"\bdoctor:", r"\bdr\.?\s?\w*:", r"\bphysician:"]
PATIENT_TURN_MARKERS = [r"\bpatient:"]


# ---------------------------------------------------------------------------
# Normalization & helpers
# ---------------------------------------------------------------------------

CONTRACTIONS = {
    r"\bi've\b": "i have", r"\bi'm\b": "i am", r"\bi'd\b": "i would",
    r"\bi'll\b": "i will", r"\bdon't\b": "do not", r"\bcan't\b": "cannot",
    r"\bwon't\b": "will not", r"\bit's\b": "it is", r"\bi've not\b": "i have not",
    r"\bdidn't\b": "did not", r"\bdoesn't\b": "does not", r"\bhasn't\b": "has not",
    r"\bhaven't\b": "have not", r"\bwasn't\b": "was not", r"\bweren't\b": "were not",
    r"\bwouldn't\b": "would not", r"\bshouldn't\b": "should not",
    r"\bcouldn't\b": "could not", r"\bcould've\b": "could have",
    r"\bshould've\b": "should have", r"\bwould've\b": "would have",
    r"\bmight've\b": "might have", r"\bwhat's\b": "what is", r"\bwho's\b": "who is",
    r"\bhe's\b": "he is", r"\bshe's\b": "she is", r"\bthey're\b": "they are",
    r"\bwe're\b": "we are", r"\byou're\b": "you are",
}

@dataclass
class SoapNote:
    subjective: list = field(default_factory=list)
    objective: list = field(default_factory=list)
    assessment: list = field(default_factory=list)
    plan: list = field(default_factory=list)
    unclassified: list = field(default_factory=list)
    # Source-traceability: which transcript line index produced each bullet.
    # Maps section name -> list of line indices parallel to the bullet lists.
    sources: Dict[str, List[int]] = field(default_factory=lambda: {
        "subjective": [], "objective": [], "assessment": [], "plan": [], "unclassified": [],
    })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subjective": self.subjective,
            "objective": self.objective,
            "assessment": self.assessment,
            "plan": self.plan,
            "unclassified": self.unclassified,
            "sources": self.sources,
        }


def _split_speaker(line: str) -> Tuple[Optional[str], str]:
    """Returns (speaker, text) if the line has a 'Doctor:'/'Patient:' prefix, else (None, line)."""
    m = re.match(r"^\s*(doctor|dr\.?\s?\w*|patient|physician)\s*:\s*(.*)", line, re.IGNORECASE)
    if m:
        speaker = "doctor" if "doctor" in m.group(1).lower() or "dr" in m.group(1).lower() or "physician" in m.group(1).lower() else "patient"
        return speaker, m.group(2).strip()
    return None, line.strip()


def _normalize(text: str) -> str:
    out = text.lower()
    for pat, repl in CONTRACTIONS.items():
        out = re.sub(pat, repl, out)
    return out


def _is_negated(text: str) -> bool:
    """Check if the line contains absence-of-finding language."""
    text_l = _normalize(text)
    return any(re.search(pat, text_l) for pat in NEGATION_PATTERNS)


def _score_line(text: str, markers: list) -> int:
    text_l = _normalize(text)
    return sum(1 for pat in markers if re.search(pat, text_l))


def _infer_speaker(text: str) -> Optional[str]:
    """Heuristic to infer whether a line is from doctor or patient when no label exists."""
    text_l = _normalize(text)

    # Patient telltales: first-person speech
    patient_hits = sum(1 for pat in PATIENT_TELLTALE_MARKERS if re.search(pat, text_l))
    # Doctor telltales: second-person speech, instructions, questions
    doctor_hits = sum(1 for pat in DOCTOR_TELLTALE_MARKERS if re.search(pat, text_l))
    is_question = any(re.search(pat, text_l) for pat in QUESTION_MARKERS)

    if is_question:
        return "doctor"
    if patient_hits > doctor_hits:
        return "patient"
    if doctor_hits > patient_hits:
        return "doctor"
    return None


def _split_multi_clause(line: str) -> List[str]:
    """Split lines with multiple clauses into separate statement segments for better classification.

    Deliberately conservative — only splits on comma-separated independent clauses or
    conjunction-joining of longer clauses. Avoids fragmenting short phrases like "I've" or
    "Okay,".
    """
    # Guard: don't split if the whole line is short
    if len(line.split()) <= 6:
        return [line]

    parts = re.split(r"\s+(?:and|but|however|while|so|thus|because|then|also)\s+", line, flags=re.IGNORECASE)

    # Only keep segments that are substantial (≥ 4 words after strip)
    result = [p.strip() for p in parts if len(p.strip().split()) >= 4]

    # Also split on commas when they separate obviously independent clauses
    if len(result) <= 1:
        comma_parts = re.split(r",\s+", line)
        comma_result = [p.strip() for p in comma_parts if len(p.strip().split()) >= 4]
        if len(comma_result) > 1:
            result = comma_result

    return result if len(result) > 1 else [line]


def _clean_bullet(text: str) -> str:
    """Clean a classified line into a concise bullet phrase."""
    text = text.strip()
    # Remove speaker prefix
    m = re.match(r"^(?:doctor|dr\.?\s?\w*|patient|physician)\s*:\s*", text, re.IGNORECASE)
    if m:
        text = text[m.end():]
    # Capitalize first letter
    return text[:1].upper() + text[1:] if text else text


def _classify_line(text: str, speaker: Optional[str]) -> str:
    """
    Core classification logic — returns section name: subjective / objective / assessment / plan / unclassified.

    Uses a weighted scoring system with clinical-signal priorities:
    1. Patient-reported symptoms (with first-person phrasing) → Subjective
    2. Measurements + exam/test findings (from doctor) → Objective
    3. Diagnosis/impression/differential language from doctor → Assessment
    4. Medication/dose/follow-up instructions → Plan
    """
    text_norm = _normalize(text)
    is_negated = _is_negated(text)
    speakers = speaker or _infer_speaker(text) or "unknown"

    # Count medication terms separately — a dose pattern alone doesn't make it a plan
    has_medication_term = _score_line(text, PLAN_MEDICATION_TERMS) > 0
    has_dose = _score_line(text, PLAN_DOSE_PATTERN) > 0
    has_action = _score_line(text, PLAN_ACTION_TERMS) > 0
    has_followup = _score_line(text, PLAN_FOLLOWUP_TERMS) > 0

    plan_strength = (
        (_score_line(text, PLAN_MEDICATION_TERMS) * 3) +
        (has_dose * 3) +
        (_score_line(text, PLAN_ACTION_TERMS) * 1.5) +
        (_score_line(text, PLAN_FOLLOWUP_TERMS) * 1.5)
    )
    # Dose + medication together = strong plan
    if has_medication_term and has_dose:
        plan_strength += 4
    if has_medication_term and has_action:
        plan_strength += 2

    objective_strength = (
        (_score_line(text, OBJECTIVE_VITALS_TERMS) * 3) +
        (_score_line(text, OBJECTIVE_MEASUREMENT_PATTERNS) * 3) +
        (_score_line(text, OBJECTIVE_EXAM_TERMS) * 2) +
        (_score_line(text, OBJECTIVE_TEST_TERMS) * 2)
    )
    # But vitals + measurement = strong objective
    has_vital_term = _score_line(text, OBJECTIVE_VITALS_TERMS) > 0
    has_measurement = any(re.search(p, text_norm) for p in OBJECTIVE_MEASUREMENT_PATTERNS)
    if has_vital_term and has_measurement:
        objective_strength += 4

    subjective_strength = (
        (_score_line(text, SUBJECTIVE_SYMPTOM_TERMS) * 2) +
        (_score_line(text, SUBJECTIVE_PHRASE_MARKERS))
    )
    # First-person symptom report = strong subjective
    has_first_person = any(re.search(p, text_norm) for p in [r"\bi (?:have|had|feel|felt|am|think|notice)\b", r"\bmy\b", r"\bi've\b"])
    if has_first_person and subjective_strength > 0:
        subjective_strength += 3

    assessment_strength = (
        (_score_line(text, ASSESSMENT_DIAGNOSIS_TERMS) * 3) +
        (_score_line(text, ASSESSMENT_PHRASE_MARKERS))
    )

    scores = {
        "subjective": subjective_strength,
        "objective": objective_strength,
        "assessment": assessment_strength,
        "plan": plan_strength,
    }

    # --- Negation handling ---
    if is_negated:
        # "No fever", "no allergies" → the *absence* is still clinically relevant clinical info.
        # If it has a symptom term, keep it in subjective (patient report) so doctor verifies.
        if subjective_strength > 0 and not plan_strength:
            scores["subjective"] += 1
        # "No allergies" — allergy is in assessment terms, but as a negated patient report
        # it belongs in subjective
        if _score_line(text, ASSESSMENT_DIAGNOSIS_TERMS) > 0 and not assessment_strength >= 4:
            scores["subjective"] += 1
        # Any negated finding with a symptom/condition term → subjective
        if not plan_strength and not objective_strength:
            scores["subjective"] += 1

    top_score = max(scores.values())
    if top_score == 0:
        return "unclassified"

    # --- Strong-signal overrides (order matters) ---
    # 1. Plan is strongest when medication + dose/action is present
    if plan_strength >= 6 and (has_medication_term or has_dose or has_action or has_followup):
        return "plan"
    # 2. Objective when vitals + measurement together
    if objective_strength >= 6 and has_vital_term and has_measurement:
        return "objective"
    # 3. Strong patient subjective report — first-person + symptom/body-part
    #    This must come BEFORE assessment so "I have acid reflux" → Subjective, not Assessment
    if subjective_strength >= 3 and has_first_person:
        return "subjective"
    # 4. Assessment language from a doctor (impression, likely, diagnosis)
    if assessment_strength >= 4 and speakers == "doctor":
        return "assessment"
    # 5. Negated finding with symptom term → Subjective (patient report of absence)
    if is_negated and subjective_strength > 0:
        return "subjective"

    candidates = [k for k, v in scores.items() if v == top_score and v > 0]

    if len(candidates) == 1:
        return candidates[0]

    # --- Tie-breaking with speaker context ---
    if speakers == "patient" and "subjective" in candidates:
        return "subjective"
    if speakers == "doctor":
        priority = ["plan", "assessment", "objective", "subjective"]
        for p in priority:
            if p in candidates:
                return p
    # For unknown speaker, prefer most clinically conservative
    priority = ["subjective", "objective", "assessment", "plan"]
    for p in priority:
        if p in candidates:
            return p

    return candidates[0]


def generate_soap_rule_based(transcript: str) -> SoapNote:
    note = SoapNote()
    lines = [l for l in transcript.split("\n") if l.strip()]

    # Source-traceability: remember which transcript line (0-based) produced each bullet.
    for line_idx, line in enumerate(lines):
        speaker, text = _split_speaker(line)
        if not text:
            continue

        segments = _split_multi_clause(text)

        for segment in segments:
            section = _classify_line(segment, speaker)
            if section == "unclassified":
                note.unclassified.append(_clean_bullet(segment))
                note.sources["unclassified"].append(line_idx)
            else:
                getattr(note, section).append(_clean_bullet(segment))
                note.sources[section].append(line_idx)

    return note


SOAP_PROMPT_TEMPLATE = """You are a clinical documentation assistant. Read this doctor-patient
consultation transcript and produce a structured SOAP note. Respond ONLY with valid JSON
in this exact shape, no other text:

{{
  "subjective": ["point 1", "point 2", ...],
  "objective": ["point 1", ...],
  "assessment": ["point 1", ...],
  "plan": ["point 1", ...]
}}

Each list item should be a short, clinically-phrased bullet point derived from the
transcript. Do not invent information that isn't in the transcript.

Transcript:
{transcript}
"""


def _parse_soap_json(text: str) -> SoapNote:
    text = text.strip()
    text = re.sub(r"^```json|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    return SoapNote(
        subjective=data.get("subjective", []),
        objective=data.get("objective", []),
        assessment=data.get("assessment", []),
        plan=data.get("plan", []),
    )


def _extract_text_from_blocks(blocks) -> str:
    """Extract text from Anthropic API response content blocks, handling
    both the simple str and the newer block-type variants."""
    if isinstance(blocks, str):
        return blocks
    if not blocks:
        return ""
    # blocks can be a list of TextBlock / ThinkingBlock / ToolUseBlock etc.
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            return text
        # Some block types (ToolUseBlock) have input instead of text
        inp = getattr(block, "input", None)
        if inp and isinstance(inp, dict):
            text = inp.get("text") or inp.get("content") or ""
            if text:
                return str(text)
    return ""


# Default Anthropic model. Override via ANTHROPIC_MODEL env var. Keep this pinned to a
# real, currently-valid model ID — "claude-sonnet-4" (no snapshot date, not a real alias)
# is NOT valid and will error on every call. Check
# https://docs.claude.com/en/docs/about-claude/models/overview for current IDs before
# changing this, since Anthropic periodically retires older snapshots.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"

# Default OpenRouter model candidates, tried in order until one succeeds. OpenRouter's
# free-tier roster changes/expires without notice (this is exactly what happened with the
# old default here — see test_free_models.py / openrouter_result.txt in this project's
# history), so a single hardcoded slug is fragile. A short fallback chain is more
# resilient. Override entirely via OPENROUTER_MODEL as a comma-separated list.
DEFAULT_OPENROUTER_MODELS = [
    "google/gemma-4-31b-it:free",          # Google's latest free model — good quality
    "z-ai/glm-5.2:free",                   # GLM 5.2 — strong general purpose
    "minimax/minimax-m3:free",             # MiniMax M3 — good reasoning
    "nvidia/nemotron-3-super-120b-a12b:free",  # NVIDIA's large free model
]


def _call_anthropic_direct(transcript: str, api_key: str) -> SoapNote:
    import anthropic
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[{"role": "user", "content": SOAP_PROMPT_TEMPLATE.format(transcript=transcript)}],
    )
    text = _extract_text_from_blocks(response.content)
    if not text:
        raise ValueError("Empty response from Anthropic API")
    return _parse_soap_json(text)


def _openrouter_model_candidates() -> List[str]:
    override = os.environ.get("OPENROUTER_MODEL")
    if override:
        return [m.strip() for m in override.split(",") if m.strip()]
    return DEFAULT_OPENROUTER_MODELS


def _call_openrouter(transcript: str, api_key: str) -> SoapNote:
    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    last_error = None
    for model in _openrouter_model_candidates():
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=1000,
                messages=[{"role": "user", "content": SOAP_PROMPT_TEMPLATE.format(transcript=transcript)}],
            )
            content = response.choices[0].message.content
            if content is None:
                raise ValueError(f"Empty response from OpenRouter model {model}")
            return _parse_soap_json(content)
        except Exception as e:
            last_error = e
            print(f"[soap_generator] OpenRouter model '{model}' failed: {type(e).__name__}: {e}")
            continue

    raise last_error or ValueError("All OpenRouter model candidates failed")


def generate_soap_llm(transcript: str, api_key: str = None):
    """
    LLM-assisted SOAP generation. Supports two providers, checked in this order:
      1. Direct Anthropic API — ANTHROPIC_API_KEY env var (keys start with sk-ant-)
      2. OpenRouter — OPENROUTER_API_KEY env var (keys start with sk-or-), tries each
         model in OPENROUTER_MODEL (or the default candidate list) in order
    Falls back to rule-based on any failure so the app never breaks without a working key.
    Returns (SoapNote, engine_used) so the UI can be honest about which one actually ran —
    silent fallback is fine, silently *claiming* LLM quality when it didn't run is not.
    """
    anthropic_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")

    if anthropic_key:
        try:
            return _call_anthropic_direct(transcript, anthropic_key), "llm_anthropic"
        except Exception as e:
            print(f"[soap_generator] Anthropic API call failed, falling back: {type(e).__name__}: {e}")

    if openrouter_key:
        try:
            return _call_openrouter(transcript, openrouter_key), "llm_openrouter"
        except Exception as e:
            print(f"[soap_generator] OpenRouter API call failed, falling back: {type(e).__name__}: {e}")

    if not anthropic_key and not openrouter_key:
        return generate_soap_rule_based(transcript), "rule_based"

    # A key was configured but the call(s) failed
    return generate_soap_rule_based(transcript), "rule_based_fallback"


def check_completeness(note: SoapNote) -> dict:
    """
    Flags likely-missing documentation instead of ever inventing it. Each check is a
    simple, explainable heuristic — deliberately conservative so it under-flags rather
    than nags about things that are actually fine.
    """
    issues = []

    has_vitals = any(
        re.search(r"\d{2,3}/\d{2,3}|\bblood pressure\b|\btemperature\b|\bheart rate\b|\bpulse\b|\d{2,3}\s?bpm", o.lower())
        for o in note.objective
    )
    if not has_vitals:
        issues.append("Vital signs not documented")

    has_medication_or_plan_detail = any(
        re.search(r"\d+\s?mg\b|\bprescribe\b|\bfollow up\b|\brefer", p.lower()) for p in note.plan
    )
    if not note.plan:
        issues.append("Plan not documented")
    elif not has_medication_or_plan_detail:
        issues.append("Plan lacks specific medication/dosage or follow-up detail")

    if not note.assessment:
        issues.append("Assessment/clinical impression not documented")

    if not note.subjective:
        issues.append("Patient-reported symptoms not documented")

    all_text = " ".join(note.subjective + note.objective).lower()
    if "allerg" not in all_text:
        issues.append("Allergies not documented")

    completeness = {
        "subjective": 100 if note.subjective else 0,
        "objective": 100 if has_vitals else (50 if note.objective else 0),
        "assessment": 100 if note.assessment else 0,
        "plan": 100 if has_medication_or_plan_detail else (50 if note.plan else 0),
    }

    return {"completeness": completeness, "issues": issues}


def _attach_source_indices(note: SoapNote, transcript: str) -> None:
    """Attach the most relevant non-empty transcript line to every note bullet."""
    lines = [line for line in transcript.split("\n") if line.strip()]
    if not lines:
        return

    stop_words = {
        "a", "an", "and", "are", "as", "be", "been", "by", "for", "from",
        "has", "have", "in", "is", "it", "of", "on", "or", "patient", "reports",
        "that", "the", "this", "to", "was", "with",
    }

    def terms(text: str) -> set:
        return {
            token for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) > 2 and token not in stop_words
        }

    line_terms = [terms(line) for line in lines]
    for section in ("subjective", "objective", "assessment", "plan", "unclassified"):
        bullets = getattr(note, section)
        existing = note.sources.get(section, [])
        if len(existing) == len(bullets):
            continue

        note.sources[section] = []
        for bullet in bullets:
            bullet_terms = terms(bullet)
            scores = [len(bullet_terms & source_terms) for source_terms in line_terms]
            note.sources[section].append(max(range(len(scores)), key=scores.__getitem__))


def generate_soap(transcript: str, use_llm: bool = False, api_key: Optional[str] = None) -> dict:
    if not transcript or not transcript.strip():
        empty = SoapNote()
        result = empty.to_dict()
        result["engine"] = "none"
        result.update(check_completeness(empty))
        return result

    if use_llm:
        note, engine = generate_soap_llm(transcript, api_key)
    else:
        note, engine = generate_soap_rule_based(transcript), "rule_based"

    _attach_source_indices(note, transcript)

    result = note.to_dict()
    result["engine"] = engine
    result.update(check_completeness(note))
    return result
