from typing import List, Dict, Any
from pydantic import BaseModel, Field
from app.common.constants import PillarType
from test_engine.pillars.base import PillarScanResult, PillarFinding
from test_engine.scoring.rubrics import DEFAULT_PILLAR_WEIGHTS, calculate_overall_score, assign_grade
from test_engine.crawl.site_crawler import redact_url

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
    reported_findings: int = 0
    findings_truncated: bool = False
    effective_pillar_weights: Dict[str, float] = Field(default_factory=dict)
    score_method: str = "weighted pillar scores, normalized over pillars that ran"

class ReportAggregator:
    """Aggregates multi-pillar results into a unified report model."""

    @staticmethod
    def aggregate(run_id: str, target_url: str, pillar_results: List[PillarScanResult]) -> UnifiedReportData:
        scores_by_pillar = {}
        results_map = {}
        all_findings = []
        total_findings = 0
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

        for pr in pillar_results:
            if pr.status != "SKIPPED":
                scores_by_pillar[pr.pillar] = pr.score
            results_map[pr.pillar.value] = pr
            total_findings += int(pr.metrics.get("finding_count", len(pr.findings)))
            for f in pr.findings:
                all_findings.append(f)
            pillar_severity_counts = pr.metrics.get("finding_severity_counts")
            if isinstance(pillar_severity_counts, dict):
                for severity_name, count in pillar_severity_counts.items():
                    severity_counts[severity_name.lower()] = severity_counts.get(severity_name.lower(), 0) + int(count)
            else:
                for f in pr.findings:
                    sev_key = f.severity.value.lower()
                    severity_counts[sev_key] = severity_counts.get(sev_key, 0) + 1

        overall_score = calculate_overall_score(scores_by_pillar)
        weight_total = sum(DEFAULT_PILLAR_WEIGHTS[pillar] for pillar in scores_by_pillar)
        effective_weights = {
            pillar.value: round(DEFAULT_PILLAR_WEIGHTS[pillar] / weight_total, 4)
            for pillar in scores_by_pillar
        } if weight_total else {}
        grade = assign_grade(overall_score)
        passed = (
            overall_score >= 70.0
            and severity_counts["critical"] == 0
            and all(result.status in {"PASSED", "SKIPPED"} for result in pillar_results)
        )

        return UnifiedReportData(
            run_id=run_id,
            target_url=redact_url(target_url),
            overall_score=overall_score,
            grade=grade,
            passed=passed,
            pillar_results=results_map,
            total_findings=total_findings,
            findings_by_severity=severity_counts,
            all_findings=all_findings,
            reported_findings=len(all_findings),
            findings_truncated=total_findings > len(all_findings),
            effective_pillar_weights=effective_weights,
        )
