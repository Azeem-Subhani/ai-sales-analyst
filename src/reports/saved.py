"""Find saved dry-run reports and prepare them for viewing in the dashboard (Week 6).

Dry runs save report.html with each chart as <chart>.png next to it (builder.save_dry_run
rewrites the email's cid: references to those files). The dashboard cannot load
relative files from inside an HTML component, so inline_images swaps each saved PNG
(and any cid: reference left over) for a data URI.
"""

import base64
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from src.reports import backfill, builder

FOLDER_NAME = re.compile(r"^(?P<report>[a-z_]+)_(?P<as_of>\d{4}-\d{2}-\d{2})$")
IMAGE_SRC = re.compile(r'src="(?:cid:)?(?P<name>[A-Za-z0-9_\-]+)(?:\.png)?"')


@dataclass(frozen=True)
class SavedReport:
    """One saved report folder."""

    source: str  # "reports" (outputs/reports) or "backfill <run date>"
    report: str
    as_of: str
    folder: Path

    @property
    def html_path(self) -> Path:
        """The saved report.html."""
        return self.folder / builder.HTML_FILENAME

    @property
    def excel_path(self) -> Path | None:
        """The saved Excel file, if this report has one."""
        path = self.folder / f"{self.report}_{self.as_of}.xlsx"
        return path if path.exists() else None


def dry_run_reports(root: Path = builder.OUTPUT_ROOT) -> list[SavedReport]:
    """Saved reports in outputs/reports, newest as_of first."""
    if not root.exists():
        return []
    found = []
    for folder in root.iterdir():
        match = FOLDER_NAME.match(folder.name)
        if match and (folder / builder.HTML_FILENAME).exists():
            found.append(SavedReport("reports", match["report"], match["as_of"], folder))
    return sorted(found, key=lambda r: (r.as_of, r.report), reverse=True)


def backfill_manifest(root: Path = backfill.BACKFILL_ROOT) -> list[dict]:
    """The backfill manifest rows (empty if no backfill has run)."""
    path = root / backfill.MANIFEST_NAME
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def backfill_reports(root: Path = backfill.BACKFILL_ROOT) -> list[SavedReport]:
    """Successful backfilled reports from the manifest, in run order."""
    return [
        SavedReport(
            f"backfill {row['run_date']}", row["report"], row["as_of"], root / row["output_folder"]
        )
        for row in backfill_manifest(root)
        if row["status"] == "ok" and row["output_folder"]
    ]


def inline_images(html: str, folder: Path) -> str:
    """The HTML with every chart reference that has a saved PNG replaced by a data URI."""

    def data_uri(match: re.Match) -> str:
        png = folder / f"{match['name']}.png"
        if not png.exists():
            return match.group(0)
        encoded = base64.b64encode(png.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{encoded}"'

    return IMAGE_SRC.sub(data_uri, html)


def report_html(saved: SavedReport) -> str:
    """A saved report's HTML, ready to show (images inlined)."""
    return inline_images(saved.html_path.read_text(encoding="utf-8"), saved.folder)
