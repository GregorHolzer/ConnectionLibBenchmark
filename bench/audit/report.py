"""The verdict sink the audit checks write into, and the thresholds they judge against.

A check never prints. It calls `report.add(...)` with a verdict and a detail string, and the
Report renders the accumulated rows twice: once to the console and once as `verification.md` next
to the data. Rows keep their insertion order, which is what makes the two passes read as two
ordered sections rather than a bag of results.
"""

from collections import defaultdict
from pathlib import Path

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
COLOURS = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[2m"}
RESET = "\033[0m"

SECTION_METHODOLOGY = "A. Methodology — does the benchmark follow good practice?"
SECTION_RELIABILITY = "B. Reliability — can the numbers be trusted?"

# Thresholds. Stated here rather than buried in the checks so they can be argued with.
MIN_REPETITIONS = 3            # below this, random and systematic error are indistinguishable
MAX_OVERHEAD_FRACTION = 0.01   # instrumentation must cost <1% of the smallest measured quantity
MAX_CV = 0.25                  # within-point coefficient of variation
MAX_BETWEEN_REP_SPREAD = 0.20  # relative spread of a point's median across repetitions
DRIFT_P = 0.01                 # significance for calling a trend systematic
MAX_OUTLIER_FRACTION = 0.05
MIN_RANK_STABILITY = 0.80      # mean Spearman correlation between repetitions
WARMUP_K = 3                   # first k exchanges checked against the rest


class Report:
    def __init__(self):
        self.rows = []

    def add(self, section, practice, name, verdict, detail):
        self.rows.append({"section": section, "practice": practice, "name": name,
                           "verdict": verdict, "detail": detail})

    @property
    def failures(self):
        return [r for r in self.rows if r["verdict"] == FAIL]

    @property
    def warnings(self):
        return [r for r in self.rows if r["verdict"] == WARN]

    def print_console(self):
        current = None
        for row in self.rows:
            if row["section"] != current:
                current = row["section"]
                print(f"\n{current}\n{'=' * len(current)}")
            colour = COLOURS[row["verdict"]]
            practice = f"[{row['practice']}]" if row["practice"] else "     "
            print(f"  {colour}{row['verdict']:<4}{RESET} {practice:<6} {row['name']}")
            for line in str(row["detail"]).split("\n"):
                if line:
                    print(f"              {line}")

    def write_markdown(self, path, results_dir):
        lines = [f"# Verification report — `{Path(results_dir).name}`", ""]
        counts = defaultdict(int)
        for row in self.rows:
            counts[row["verdict"]] += 1
        lines.append(f"**{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed, "
                     f"{counts[SKIP]} skipped.**")
        lines.append("")
        lines.append("Practice IDs refer to [docs/benchmarking-practices.md]"
                     "(../../docs/benchmarking-practices.md), which summarises "
                     "`02_experimentation.pdf` (Peter Thoman, University of Innsbruck).")
        lines.append("")

        current = None
        for row in self.rows:
            if row["section"] != current:
                current = row["section"]
                lines += ["", f"## {current}", "",
                          "| | Practice | Check | Detail |", "|---|---|---|---|"]
            detail = str(row["detail"]).replace("\n", "<br>").replace("|", "\\|")
            lines.append(f"| **{row['verdict']}** | {row['practice'] or '—'} | {row['name']} "
                         f"| {detail} |")
        lines.append("")
        Path(path).write_text("\n".join(lines))
