"""Archive standalone validation reports without replacing earlier runs."""
from __future__ import annotations

import shutil
from pathlib import Path


def backup_validation_report(report_root: Path, report_file: Path, timestamp: str) -> Path:
    """Copy a completed validation report into backup/ using a unique timestamped name."""
    backup_dir = report_root / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"validation_report_{timestamp}.html"
    duplicate = 1
    while destination.exists():
        destination = backup_dir / f"validation_report_{timestamp}-{duplicate}.html"
        duplicate += 1
    shutil.copy2(report_file, destination)
    return destination
