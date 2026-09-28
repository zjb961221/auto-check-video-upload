"""Compatibility exports and CSV export for the desktop application."""
import csv
import os
from pathlib import Path
import tempfile
from queries import MAX_ROWS, load_queries
from database import run_query


def export_csv(path, columns, rows):
    def safe(value):
        value = '' if value is None else str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8-sig',
                                         dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow([safe(v) for v in columns])
            writer.writerows([safe(v) for v in row] for row in rows)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
