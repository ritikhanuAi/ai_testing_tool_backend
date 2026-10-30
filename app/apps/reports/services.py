import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any
from app.apps.reports.models import ReportSummary
from app.apps.test_runs.models import PillarResult, TestFinding

async def save_report(db: AsyncSession, test_run_id: str, report_data: Dict[str, Any]) -> ReportSummary:
    """Save finalized 4-pillar report summary, normalized pillar results, and granular findings."""
    pillar_res = report_data.get("pillar_results", {})
    sev_counts = report_data.get("findings_by_severity", {})

    def pillar_score(name: str) -> float:
        result = pillar_res.get(name, {})
        if isinstance(result, dict):
            if result.get("status") == "SKIPPED":
                return 0.0
            return float(result.get("score", 100.0))
        if getattr(result, "status", None) == "SKIPPED":
            return 0.0
        return float(getattr(result, "score", 100.0))

    func_score = pillar_score("functional")
    sec_score = pillar_score("security")
    perf_score = pillar_score("performance")
    a11y_score = pillar_score("accessibility")

    report = ReportSummary(
        test_run_id=test_run_id,
        overall_score=report_data.get("overall_score", 0.0),
        grade=report_data.get("grade", "F"),
        passed=report_data.get("passed", False),
        functional_score=func_score,
        security_score=sec_score,
        performance_score=perf_score,
        accessibility_score=a11y_score,
        total_findings=report_data.get("total_findings", 0),
        critical_findings=sev_counts.get("critical", 0),
        high_findings=sev_counts.get("high", 0),
        medium_findings=sev_counts.get("medium", 0),
        low_findings=sev_counts.get("low", 0),
        report_data=report_data
    )
    db.add(report)

    # Persist normalized PillarResult and TestFinding records
    for pillar_name, p_data in pillar_res.items():
        if isinstance(p_data, dict):
            p_score = p_data.get("score", 100.0)
            p_status = p_data.get("status", "PASSED")
            p_duration = p_data.get("duration_sec", 0.0)
            p_metrics = p_data.get("metrics", {})
            findings_list = p_data.get("findings", [])
        else:
            p_score = getattr(p_data, "score", 100.0)
            p_status = getattr(p_data, "status", "PASSED")
            p_duration = getattr(p_data, "duration_sec", 0.0)
            p_metrics = getattr(p_data, "metrics", {})
            findings_list = getattr(p_data, "findings", [])

        pillar_record = PillarResult(
            test_run_id=test_run_id,
            pillar=str(pillar_name),
            score=float(p_score),
            status=str(p_status),
            duration_sec=float(p_duration),
            findings_count=len(findings_list),
            metrics=p_metrics if isinstance(p_metrics, dict) else {}
        )
        db.add(pillar_record)
        await db.flush()  # Generate pillar_record.id

        for finding in findings_list:
            if isinstance(finding, dict):
                f_id = finding.get("id", "FINDING")
                f_title = finding.get("title", "")
                f_desc = finding.get("description", "")
                f_sev = str(finding.get("severity", "info")).lower()
                f_fp = finding.get("fingerprint", "")
                f_url = finding.get("selector_or_url")
                f_fix = finding.get("suggested_fix")
                f_ev = finding.get("evidence_path")
            else:
                f_id = getattr(finding, "id", "FINDING")
                f_title = getattr(finding, "title", "")
                f_desc = getattr(finding, "description", "")
                f_sev = str(getattr(finding, "severity", "info")).lower()
                f_fp = getattr(finding, "fingerprint", "")
                f_url = getattr(finding, "selector_or_url", None)
                f_fix = getattr(finding, "suggested_fix", None)
                f_ev = getattr(finding, "evidence_path", None)

            if not f_fp:
                f_fp = hashlib.sha1(f"{f_id}:{f_url or ''}:{f_title}".encode()).hexdigest()[:16]

            finding_record = TestFinding(
                test_run_id=test_run_id,
                pillar_result_id=pillar_record.id,
                rule_id=f_id,
                fingerprint=f_fp,
                pillar=str(pillar_name),
                severity=f_sev,
                title=f_title,
                description=f_desc,
                selector_or_url=f_url,
                suggested_fix=f_fix,
                evidence_path=f_ev
            )
            db.add(finding_record)

    await db.commit()
    await db.refresh(report)
    return report

