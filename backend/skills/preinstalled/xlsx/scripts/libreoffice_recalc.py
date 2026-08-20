#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
libreoffice_recalc.py — Tier 2 dynamic formula recalculation via LibreOffice headless.

Opens the xlsx file with the LibreOffice Calc engine, executes all formulas, writes
the computed values into the <v> cache elements, and saves the result. This is the
closest server-side equivalent of "open in Excel and save."

Key correctness guarantees (this version):
  1. FORCED RECALC — by default LibreOffice does NOT recompute OOXML (xlsx) formulas on
     load (its OOXMLRecalcMode defaults to "never recalc on load"), so freshly written
     formula cells stay empty. We write an isolated user profile with
     `OOXMLRecalcMode=0` (always recalc) so values are actually computed.
  2. ISOLATED PROFILE — each run uses its own `-env:UserInstallation` profile, so a
     leftover lock from a crashed run can never block a new run.
  3. ORPHAN CLEANUP — any soffice process whose command line references our profile is
     killed before launch and again on exit (covers timeouts that leave children behind).
  4. POST-CHECK — after recalc we verify that formula cells actually received cached
     <v> values. A run that exits 0 but ships empty formula cells is reported as a
     HARD FAILURE, not a silent success.

Usage:
    python3 libreoffice_recalc.py input.xlsx output.xlsx
    python3 libreoffice_recalc.py input.xlsx output.xlsx --timeout 90
    python3 libreoffice_recalc.py --check          # check LibreOffice availability only

Exit codes:
    0 — recalculation succeeded AND formula cells were populated, output written
    2 — LibreOffice not found (Tier 2 unavailable — not a hard failure, note in report)
    1 — LibreOffice found but recalculation failed (timeout, crash, OR empty <v> cache)
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

# OOXML SpreadsheetML namespace
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NSP = f"{{{_NS}}}"

# Fraction of formula cells that must have a populated <v> for the recalc to count
# as successful. LibreOffice should fill ~100%; 0.9 leaves margin for edge cases.
DEFAULT_MIN_POPULATED_RATIO = 0.9


# ── LibreOffice discovery ───────────────────────────────────────────────────

def find_soffice() -> str | None:
    """
    Locate the soffice (LibreOffice) binary.

    Search order:
      1. macOS application bundle (default install location)
      2. PATH lookup for 'soffice'
      3. PATH lookup for 'libreoffice' (common on Linux)
    """
    candidates = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",  # macOS
        "soffice",     # Linux / macOS if on PATH
        "libreoffice", # alternative Linux name
    ]
    for c in candidates:
        # shutil.which handles PATH lookup; also check absolute paths directly
        found = shutil.which(c)
        if found:
            return found
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def get_libreoffice_version(soffice: str) -> str:
    """Return LibreOffice version string, or 'unknown' on failure."""
    try:
        result = subprocess.run(
            [soffice, "--version"],
            capture_output=True,
            timeout=10,
        )
        return result.stdout.decode(errors="replace").strip()
    except Exception:
        return "unknown"


# ── Isolated profile + forced recalc ────────────────────────────────────────

def _write_recalc_profile(profile_dir: str) -> None:
    """
    Create an isolated LibreOffice user profile that forces formula recalculation
    on load. Without this, LibreOffice keeps the (empty) cached values from a
    freshly generated xlsx and ships blank formula cells.
    """
    user_dir = os.path.join(profile_dir, "user")
    os.makedirs(user_dir, exist_ok=True)
    xcu = os.path.join(user_dir, "registrymodifications.xcu")
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        ' <item oor:path="/org.openoffice.Office.Calc/Formula/Load">\n'
        '  <prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>\n'
        '  <prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop>\n'
        " </item>\n"
        "</oor:items>\n"
    )
    with open(xcu, "w", encoding="utf-8") as f:
        f.write(content)


def _kill_matching_procs(pattern: str) -> int:
    """
    Kill lingering soffice processes whose command line contains `pattern`
    (typically our isolated profile path). Returns number of processes signaled.
    Best-effort: any error is ignored.
    """
    killed = 0
    pids: set[str] = set()

    # Preferred: pgrep
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=10,
        )
        for p in out.stdout.split():
            p = p.strip()
            if p.isdigit():
                pids.add(p)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: scan `ps` (e.g. systems without pgrep)
    if not pids:
        try:
            out = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True, text=True, timeout=10,
            )
            for line in out.stdout.splitlines():
                if pattern in line:
                    pid = line.strip().split()[0]
                    if pid.isdigit():
                        pids.add(pid)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGTERM)
            killed += 1
        except (ProcessLookupError, PermissionError, ValueError):
            pass

    if killed:
        # Give them a moment to exit before we proceed.
        time.sleep(1.0)
    return killed


# ── Post-recalculation verification ─────────────────────────────────────────

def verify_recalculated(path: str, min_populated_ratio: float = DEFAULT_MIN_POPULATED_RATIO):
    """
    Verify that formula cells actually received cached <v> values.

    Returns:
        (ok: bool, detail: str)

    A freshly generated xlsx has formula cells with <f> but no <v>. If LibreOffice
    failed to recalc (its default behavior without the forced-recalc profile), the
    <v> elements stay empty. We treat that as a hard failure so the caller knows the
    file would show blank formula cells in Excel/WPS.
    """
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:  # noqa: BLE001 - surface any open failure clearly
        return False, f"cannot open recalc output for verification: {e}"

    formula_cells = 0
    populated = 0
    with z:
        for name in z.namelist():
            if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
                continue
            try:
                root = ET.fromstring(z.read(name))
            except Exception:  # noqa: BLE001 - skip malformed sheet, keep scanning
                continue
            for c in root.findall(f".//{_NSP}c"):
                f = c.find(f"{_NSP}f")
                if f is None:
                    continue
                formula_cells += 1
                v = c.find(f"{_NSP}v")
                if v is not None and v.text is not None and v.text.strip() != "":
                    populated += 1

    if formula_cells == 0:
        # No formulas to verify (e.g. a report that writes values directly).
        return True, "no formula cells to verify (0 formulas present)"

    ratio = populated / formula_cells
    if ratio < min_populated_ratio:
        return False, (
            f"recalc produced no cached values: only {populated}/{formula_cells} "
            f"formula cells have a <v> result ({ratio:.0%}). LibreOffice did not "
            f"evaluate the formulas (values would appear blank in Excel). "
            f"Do NOT deliver this file — fall back to writing computed values directly."
        )
    return True, f"{populated}/{formula_cells} formula cells populated ({ratio:.0%})"


# ── Recalculation ────────────────────────────────────────────────────────────

def recalculate(
    input_path: str,
    output_path: str,
    timeout: int = 60,
    min_populated_ratio: float = DEFAULT_MIN_POPULATED_RATIO,
) -> tuple[bool, str]:
    """
    Run LibreOffice headless recalculation on input_path, write result to output_path.

    Returns:
        (success: bool, message: str)

    The message explains what happened (success or failure reason). On success the
    message also includes the post-check summary.
    """
    soffice = find_soffice()
    if not soffice:
        return False, (
            "LibreOffice not found. Tier 2 validation is unavailable in this environment. "
            "Install LibreOffice to enable dynamic formula recalculation.\n"
            "  macOS:  brew install --cask libreoffice\n"
            "  Linux:  sudo apt-get install -y libreoffice"
        )

    version = get_libreoffice_version(soffice)

    tmpdir = tempfile.mkdtemp(prefix="xlsx_recalc_")
    profile_dir = os.path.join(tmpdir, "lo_profile")
    try:
        # 1. Isolated profile with forced recalc on load.
        _write_recalc_profile(profile_dir)

        # 2. Clean up any orphan soffice from a previously crashed run of this script.
        _kill_matching_procs(profile_dir)

        # 3. Work on a copy so the source file is never touched.
        tmp_input = os.path.join(tmpdir, os.path.basename(input_path))
        shutil.copy(input_path, tmp_input)

        cmd = [
            soffice,
            f"-env:UserInstallation=file://{profile_dir}",
            "--headless",
            "--norestore",           # do not attempt to restore crashed sessions
            "--infilter=Calc MS Excel 2007 XML",
            "--convert-to", "xlsx",
            "--outdir", tmpdir,
            tmp_input,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # Kill the whole process tree by profile pattern (run() does not kill it).
            _kill_matching_procs(profile_dir)
            return False, (
                f"LibreOffice timed out after {timeout}s. "
                "The file may be too large or contain constructs that cause LibreOffice to hang. "
                "Try increasing --timeout or simplify the file."
            )
        except FileNotFoundError:
            return False, f"LibreOffice binary not executable: {soffice}"

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            stdout = result.stdout.decode(errors="replace").strip()
            return False, (
                f"LibreOffice exited with code {result.returncode}.\n"
                f"stderr: {stderr}\n"
                f"stdout: {stdout}"
            )

        # LibreOffice writes: <tmpdir>/<stem>.xlsx
        stem = os.path.splitext(os.path.basename(tmp_input))[0]
        tmp_output = os.path.join(tmpdir, stem + ".xlsx")

        if not os.path.isfile(tmp_output):
            # Try to find any .xlsx file in tmpdir (LibreOffice may behave differently)
            xlsx_files = [
                f for f in os.listdir(tmpdir)
                if f.endswith(".xlsx") and f != os.path.basename(tmp_input)
            ]
            if xlsx_files:
                tmp_output = os.path.join(tmpdir, xlsx_files[0])
            else:
                stdout = result.stdout.decode(errors="replace").strip()
                return False, (
                    f"LibreOffice succeeded (exit 0) but output file not found in {tmpdir}.\n"
                    f"stdout: {stdout}\n"
                    f"Files in tmpdir: {os.listdir(tmpdir)}"
                )

        # Copy recalculated file to final destination
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        shutil.copy(tmp_output, output_path)

        # 4. POST-CHECK — never ship empty formula cells.
        ok, detail = verify_recalculated(output_path, min_populated_ratio)
        if not ok:
            return False, detail
        return True, f"Recalculation complete. LibreOffice {version}. {detail}"

    finally:
        # Always reap orphans and remove the temp area.
        _kill_matching_procs(profile_dir)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LibreOffice headless formula recalculation for xlsx files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic recalculation
  python3 libreoffice_recalc.py report.xlsx report_recalc.xlsx

  # With extended timeout for large files
  python3 libreoffice_recalc.py big_model.xlsx big_model_recalc.xlsx --timeout 120

  # Check if LibreOffice is available (useful in CI)
  python3 libreoffice_recalc.py --check

  # Full validation pipeline
  python3 libreoffice_recalc.py input.xlsx /tmp/recalc.xlsx && \\
    python3 formula_check.py /tmp/recalc.xlsx
""",
    )
    parser.add_argument("input", nargs="?", help="Input xlsx file path")
    parser.add_argument("output", nargs="?", help="Output xlsx file path (recalculated)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Maximum time to wait for LibreOffice (default: 60)",
    )
    parser.add_argument(
        "--min-populated",
        type=float,
        default=DEFAULT_MIN_POPULATED_RATIO,
        metavar="RATIO",
        help="Minimum fraction of formula cells that must have a cached <v> "
             "for the recalc to count as successful (default: 0.9)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check if LibreOffice is available, then exit",
    )

    args = parser.parse_args()

    # ── --check mode ─────────────────────────────────────────────────────────
    if args.check:
        soffice = find_soffice()
        if soffice:
            version = get_libreoffice_version(soffice)
            print(f"LibreOffice available: {soffice}")
            print(f"Version: {version}")
            sys.exit(0)
        else:
            print("LibreOffice NOT available.")
            print("Tier 2 dynamic validation requires LibreOffice.")
            print("  macOS:  brew install --cask libreoffice")
            print("  Linux:  sudo apt-get install -y libreoffice")
            sys.exit(2)

    # ── Recalculation mode ────────────────────────────────────────────────────
    if not args.input or not args.output:
        parser.print_help()
        sys.exit(1)

    if not os.path.isfile(args.input):
        print(f"ERROR: Input file not found: {args.input}")
        sys.exit(1)

    print(f"Input  : {args.input}")
    print(f"Output : {args.output}")
    print(f"Timeout: {args.timeout}s")
    print()

    success, message = recalculate(
        args.input, args.output,
        timeout=args.timeout,
        min_populated_ratio=args.min_populated,
    )

    if success:
        print(f"OK: {message}")
        print()
        print("Next step: run formula_check.py on the recalculated file to detect runtime errors:")
        print(f"  python3 formula_check.py {args.output}")
        sys.exit(0)
    else:
        # Distinguish "not installed" (exit 2) from "failed" (exit 1)
        if "not found" in message.lower() or "not available" in message.lower():
            print(f"SKIP (Tier 2 unavailable): {message}")
            sys.exit(2)
        else:
            print(f"ERROR: {message}")
            sys.exit(1)


if __name__ == "__main__":
    main()
