
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Medication:
    """One medication line, after rule-based or LLM extraction."""

    raw: str
    name: str | None = None            # "Amoxicillin" / "Amoxil"
    generic: str | None = None         # resolved generic, when known
    strength: str | None = None        # "500 mg" as displayed
    strength_value: float | None = None  # 500.0, for dose arithmetic
    strength_unit: str | None = None     # "mg", for dose arithmetic
    form: str | None = None            # "tablet"
    route: str | None = None           # "by mouth"
    dose_amount: str | None = None     # "1 tablet", "5 ml"
    dose_pattern: str | None = None    # "1-0-1" as written
    frequency_code: str | None = None  # "BD", "1-0-1", "Q8H"
    frequency_human: str | None = None # "twice a day (morning and night)"
    times_per_day: float | None = None # number of administrations per day
    units_per_day: float | None = None # total dose units per day (1-0-1 -> 2.0)
    timing: str | None = None          # "after food"
    duration: str | None = None        # "5 days"
    prn: bool = False                  # taken only when needed (SOS/PRN)
    purpose: str | None = None         # why it was prescribed (from KB/label)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedPrescription:
    """Result of rule-based parsing of a full prescription block."""

    raw_text: str
    medications: list[Medication] = field(default_factory=list)
    general_instructions: list[str] = field(default_factory=list)
    unparsed_lines: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Share of drug-bearing lines the parser understood.

        Section headers and general-advice lines are excluded from the
        denominator, so this measures parser recall on medication lines only.
        """
        drug_lines = len(self.medications) + len(self.unparsed_lines)
        if drug_lines == 0:
            return 0.0
        return len(self.medications) / drug_lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "medications": [m.to_dict() for m in self.medications],
            "general_instructions": self.general_instructions,
            "unparsed_lines": self.unparsed_lines,
            "coverage": round(self.coverage, 3),
        }


@dataclass
class SafetyFlag:
    """A red flag raised by the deterministic safety layer."""

    kind: str        # "max_dose" | "interaction" | "duplicate" | "high_risk" | "ambiguous"
    severity: str    # "info" | "caution" | "warning"
    message: str
    subjects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Explanation:
    """Output of any one of the three systems, for one prescription."""

    case_id: str
    system: str
    input_text: str
    output_text: str
    medications: list[Medication] = field(default_factory=list)
    safety_flags: list[SafetyFlag] = field(default_factory=list)
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    retrieved: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "system": self.system,
            "input_text": self.input_text,
            "output_text": self.output_text,
            "medications": [m.to_dict() for m in self.medications],
            "safety_flags": [f.to_dict() for f in self.safety_flags],
            "latency_s": round(self.latency_s, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "retrieved": self.retrieved,
            "error": self.error,
            "meta": self.meta,
        }


@dataclass
class GoldCase:
    """One curated evaluation case."""

    case_id: str
    input_text: str
    reference: str
    drugs: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    difficulty: str = "easy"       # easy | medium | hard
    category: str = "general"      # e.g. antibiotic, chronic, pediatric, high_risk
    notes: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "GoldCase":
        return GoldCase(
            case_id=d["case_id"],
            input_text=d["input_text"],
            reference=d["reference"],
            drugs=d.get("drugs", []),
            must_include=d.get("must_include", []),
            difficulty=d.get("difficulty", "easy"),
            category=d.get("category", "general"),
            notes=d.get("notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
