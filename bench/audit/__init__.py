"""The campaign audit, split into the two passes verify_practices.py runs.

Pass A (environment, design) checks the benchmark against the practices in
docs/benchmarking-practices.md. Pass B (stability, claims) checks the data it produced. Import
order inside each pass is the report order, so the module split is also the order the checks
appear in verification.md.
"""

from . import claims, design, environment, stability
from .report import (FAIL, PASS, SECTION_METHODOLOGY, SECTION_RELIABILITY, SKIP, WARN, Report)

__all__ = ["claims", "design", "environment", "stability", "Report", "PASS", "WARN", "FAIL",
           "SKIP", "SECTION_METHODOLOGY", "SECTION_RELIABILITY"]
