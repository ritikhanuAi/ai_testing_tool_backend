from typing import List, Dict, Any
from pydantic import BaseModel, Field
from app.common.constants import PillarType
from test_engine.pillars.base import PillarScanResult, PillarFinding
from test_engine.scoring.rubrics import calculate_overall_score, assign_grade

class UnifiedReportData(BaseModel):
    run_id: str
    target_url: str
    overall_score: float
    grade: str
    passed: bool
    pillar_results: Dict[str, PillarScanResult]
    total_findings: int
    findings_by_severity: Dict[str, int]
    all_findings: List[PillarFinding]

class ReportAggregator:
    """Aggregates multi-pillar results into a unified report model."""

    @staticmethod
    def aggregate(run_id: str, target_url: str, pillar_results: List[PillarScanResult]) -> UnifiedReportData:
        scores_by_pillar = {}
        results_map = {}
        all_findings = []
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

        for pr in pillar_results:
            scores_by_pillar[pr.pillar] = pr.score
            results_map[pr.pillar.value] = pr
            for f in pr.findings:
                all_findings.append(f)
                sev_key = f.severity.value.lower()
                severity_counts[sev_key] = severity_counts.get(sev_key, 0) + 1

        overall_score = calculate_overall_score(scores_by_pillar)
        grade = assign_grade(overall_score)
        passed = overall_score >= 70.0 and severity_counts["critical"] == 0

        return UnifiedReportData(
            run_id=run_id,
            target_url=target_url,
            overall_score=overall_score,
            grade=grade,
            passed=passed,
            pillar_results=results_map,
            total_findings=len(all_findings),
            findings_by_severity=severity_counts,
            all_findings=all_findings
        )
