import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from ..config import settings
from ..models.property import PropertyAnalysisResult, PropertyJob

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates JSON and HTML property assessment reports."""

    def __init__(self):
        self.output_dir = Path(settings.report_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, job: PropertyJob) -> Dict[str, str]:
        """Returns dict with paths to json_report and html_report."""
        json_path = self._write_json(job)
        html_path = self._write_html(job)
        return {"json_report": json_path, "html_report": html_path}

    def _write_json(self, job: PropertyJob) -> str:
        path = self.output_dir / f"{job.job_id}_report.json"
        data = {
            "job_id": job.job_id,
            "property_id": job.property_id,
            "latitude": job.latitude,
            "longitude": job.longitude,
            "generated_at": datetime.utcnow().isoformat(),
            "result": job.result.model_dump() if job.result else None,
            "stages": [s.model_dump() for s in job.stages],
        }
        path.write_text(json.dumps(data, indent=2))
        return str(path)

    def _write_html(self, job: PropertyJob) -> str:
        path = self.output_dir / f"{job.job_id}_report.html"
        r = job.result

        decision_color = {
            "APPROVED": "#16a34a",
            "REJECTED": "#dc2626",
            "NEEDS_HUMAN_REVIEW": "#d97706",
        }.get(r.decision.value if r else "", "#6b7280")

        obs_rows = ""
        if r:
            for k, v in r.observations.model_dump().items():
                obs_rows += f"<tr><td>{k.replace('_', ' ').title()}</td><td>{v}</td></tr>"

        rejection_html = ""
        if r and r.rejection_reasons:
            items = "".join(f"<li>{x}</li>" for x in r.rejection_reasons)
            rejection_html = f"<h3>Rejection Reasons</h3><ul>{items}</ul>"

        review_html = ""
        if r and r.human_review_reasons:
            items = "".join(f"<li>{x}</li>" for x in r.human_review_reasons)
            review_html = f"<h3>Human Review Flags</h3><ul>{items}</ul>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Property Report — {job.job_id}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto; color: #111; }}
    .badge {{ display: inline-block; padding: 6px 18px; border-radius: 20px; color: #fff; font-weight: bold; background: {decision_color}; font-size: 1.1rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
    td, th {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f3f4f6; }}
    h2 {{ border-bottom: 2px solid #e5e7eb; padding-bottom: 8px; }}
    .summary {{ background: #f9fafb; border-left: 4px solid {decision_color}; padding: 12px 16px; margin: 16px 0; }}
  </style>
</head>
<body>
  <h1>Property Intelligence Report</h1>
  <p><strong>Job ID:</strong> {job.job_id}</p>
  <p><strong>Coordinates:</strong> {job.latitude:.6f}, {job.longitude:.6f}</p>
  <p><strong>Generated:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>

  <h2>Decision</h2>
  <span class="badge">{r.decision.value if r else 'N/A'}</span>
  <p><strong>Property Type:</strong> {r.property_type.value if r else 'N/A'}</p>
  <p><strong>Confidence Score:</strong> {f'{r.confidence_score:.0%}' if r else 'N/A'}</p>

  <div class="summary">{r.summary if r else 'No analysis available.'}</div>

  {rejection_html}
  {review_html}

  <h2>Observations</h2>
  <table>
    <tr><th>Field</th><th>Value</th></tr>
    {obs_rows}
  </table>

  <h2>Pipeline Stages</h2>
  <table>
    <tr><th>Stage</th><th>Status</th><th>Duration</th></tr>
    {''.join(f'<tr><td>{s.name}</td><td>{s.status.value}</td><td>{s.duration_ms:.0f}ms</td></tr>' for s in job.stages if s.duration_ms)}
  </table>
</body>
</html>"""
        path.write_text(html)
        return str(path)
