"""PDF report generation using Jinja2 + WeasyPrint."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def generate_pdf(records: list[dict], output_path: str, title: str = "Sharing Audit Report", user_label: str = "", webapp_url: str = "") -> str:
    """Generate a styled PDF report. Falls back to HTML if WeasyPrint fails. Returns output path."""
    sorted_records = sorted(records, key=lambda r: (-r.get("risk_score", 0), RISK_ORDER.get(r.get("risk_level", "LOW"), 2)))

    high_count = sum(1 for r in records if r.get("risk_level") == "HIGH")
    medium_count = sum(1 for r in records if r.get("risk_level") == "MEDIUM")
    low_count = sum(1 for r in records if r.get("risk_level") == "LOW")

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    html = template.render(
        title=title,
        user_label=user_label,
        webapp_url=webapp_url,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        records=sorted_records,
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
    )

    try:
        HTML(string=html).write_pdf(output_path)
        logger.info(f"PDF generated: {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"WeasyPrint PDF failed: {e}")

    # Fallback: save as HTML
    fallback = output_path.replace(".pdf", ".html")
    with open(fallback, "w", encoding="utf-8") as f:
        f.write(html)
    logger.warning(f"Saved as HTML fallback: {fallback}")
    return fallback
