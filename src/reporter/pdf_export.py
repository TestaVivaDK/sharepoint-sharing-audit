"""PDF report generation using Jinja2 + Chromium headless."""

import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def generate_pdf(records: list[dict], output_path: str, title: str = "Sharing Audit Report", user_label: str = "") -> str:
    """Generate a styled PDF report. Falls back to HTML if no PDF converter available. Returns output path."""
    sorted_records = sorted(records, key=lambda r: RISK_ORDER.get(r.get("risk_level", "LOW"), 2))

    high_count = sum(1 for r in records if r.get("risk_level") == "HIGH")
    medium_count = sum(1 for r in records if r.get("risk_level") == "MEDIUM")
    low_count = sum(1 for r in records if r.get("risk_level") == "LOW")

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    html = template.render(
        title=title,
        user_label=user_label,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        records=sorted_records,
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
    )

    html_path = f"{output_path}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Try chromium headless
    chromium = _find_chromium()
    if chromium:
        try:
            subprocess.run(
                [chromium, "--headless", "--disable-gpu", "--no-sandbox",
                 f"--print-to-pdf={output_path}", "--no-pdf-header-footer", html_path],
                capture_output=True, timeout=60,
            )
            if os.path.exists(output_path):
                os.remove(html_path)
                return output_path
        except Exception as e:
            logger.warning(f"Chromium PDF failed: {e}")

    # Fallback: return HTML
    fallback = output_path.replace(".pdf", ".html")
    if html_path != fallback:
        os.rename(html_path, fallback)
    logger.warning(f"No PDF converter. Saved as HTML: {fallback}")
    return fallback


def _find_chromium() -> str | None:
    for cmd in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if shutil.which(cmd):
            return cmd
    return None
