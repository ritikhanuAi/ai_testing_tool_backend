from typing import List, Tuple, Dict, Any
from app.common.constants import PillarType, FindingSeverity
from test_engine.pillars.base import PillarFinding

# Standard weights per proposal
DEFAULT_PILLAR_WEIGHTS = {
    PillarType.FUNCTIONAL: 0.35,
    PillarType.SECURITY: 0.25,
    PillarType.PERFORMANCE: 0.20,
    PillarType.ACCESSIBILITY: 0.20
}

SEVERITY_DEDUCTIONS = {
    FindingSeverity.CRITICAL: 15.0,
    FindingSeverity.HIGH: 8.0,
    FindingSeverity.MEDIUM: 3.0,
    FindingSeverity.LOW: 1.0,
    FindingSeverity.INFO: 0.0,
}

def calculate_pillar_deductions(findings: List[PillarFinding], max_occurrences_per_rule: int = 3) -> Tuple[float, List[Dict[str, Any]]]:
    """Calculate transparent score deductions capped per rule occurrence.
    
    Formula: pillar_score = max(0, 100 - sum(deductions))
    Deduction caps: Max 3 deductions per unique rule_id.
    """
    rule_counts: Dict[str, int] = {}
    deductions_list: List[Dict[str, Any]] = []
    total_deductions = 0.0

    for f in findings:
        count = rule_counts.get(f.id, 0)
        if count < max_occurrences_per_rule:
            rule_counts[f.id] = count + 1
            deduction = SEVERITY_DEDUCTIONS.get(f.severity, 0.0)
            total_deductions += deduction
            deductions_list.append({
                "finding_id": f.id,
                "title": f.title,
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                "deduction": deduction,
                "fingerprint": f.fingerprint
            })

    score = max(0.0, round(100.0 - total_deductions, 1))
    return score, deductions_list

def calculate_overall_score(pillar_scores: dict) -> float:
    """Calculate weighted composite score from 4 pillar scores."""
    total_score = 0.0
    for pillar, weight in DEFAULT_PILLAR_WEIGHTS.items():
        score = pillar_scores.get(pillar, 100.0)
        total_score += score * weight
    return round(total_score, 1)

def assign_grade(score: float) -> str:
    """Assign letter grade to numerical score."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"
