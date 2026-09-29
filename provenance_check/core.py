"""Rules P1..P7: can the claim "this constant came from that dataset" be refused?

Finding schema is the family's (fiducial's signals.py): status, confidence,
rule, message, optional fix{applicability, apply}. `fix` is present only when a
repair exists. Written independently -- nothing here imports fiducial.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CannotCheck(Exception):
    """The declaration cannot be evaluated at all (exit 2)."""


@dataclass
class Report:
    findings: list[dict[str, Any]] = field(default_factory=list)
    checked: int = 0

    @property
    def violations(self) -> int:
        return sum(f["status"] == "violation" for f in self.findings)

    @property
    def cannot_check(self) -> int:
        return sum(f["status"] == "cannot_check" for f in self.findings)

    @property
    def exit_code(self) -> int:
        if self.violations:
            return 1
        return 2 if self.cannot_check else 0

    def summary(self) -> dict[str, int]:
        return {"checked": self.checked, "violations": self.violations,
                "cannot_check": self.cannot_check}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _finding(key: str, rule: str, message: str, *, confidence: str = "certain",
             status: str = "violation", fix: tuple[str, str] | None = None) -> dict:
    f: dict[str, Any] = {"status": status, "confidence": confidence, "rule": rule,
                         "constant": key, "message": message}
    if fix:
        f["fix"] = {"applicability": fix[0], "apply": fix[1]}
    return f


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise CannotCheck(f"file not found: {path}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        raise CannotCheck(f"cannot parse {path}: {e}") from None


def load_value_source(path: Path) -> dict:
    """json or toml, by suffix."""
    if path.suffix.lower() == ".json":
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if path.suffix.lower() == ".toml":
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    raise ValueError(f"unsupported suffix {path.suffix!r} (json or toml only)")


def resolve_value_ref(ref: str, base: Path) -> float:
    """Return the number at `<file>:<dotted.key>`; ValueError says why not."""
    fname, sep, dotted = ref.rpartition(":")
    if not sep or not fname or not dotted:
        raise ValueError(f"value_ref {ref!r} is not <file>:<dotted.key>")
    p = base / fname
    if not p.is_file():
        raise ValueError(f"value_ref file not found: {fname}")
    try:
        node: Any = load_value_source(p)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"value_ref file {fname} unreadable: {e}") from None
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"key {dotted!r} not found in {fname}")
        node = node[part]
    if not _num(node):
        raise ValueError(f"{dotted!r} in {fname} is not a number ({node!r})")
    return float(node)


def _parse_date(v: Any) -> _dt.date:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str):
        return _dt.date.fromisoformat(v.strip())
    raise ValueError(f"not a date: {v!r}")


def _check_constant(key: str, c: dict, base: Path, today: _dt.date) -> list[dict]:
    out: list[dict] = []
    value = c.get("value")
    have_value = _num(value)
    if not have_value:
        out.append(_finding(key, "metadata-missing",
                            f"{key}: `value` is missing or not a finite number ({value!r}); "
                            "a constant without its value cannot be tied to anything."))

    # P1 / P2: source + hash
    src = c.get("source")
    if not isinstance(src, str) or not src or not (base / src).is_file():
        out.append(_finding(key, "source-missing",
                            f"{key}: source file {src!r} does not exist; the constant claims "
                            "a dataset nobody can open, so the claim cannot be checked or "
                            "reproduced."))
    else:
        actual = sha256_of(base / src)
        pinned = c.get("sha256")
        if not isinstance(pinned, str) or not pinned.strip():
            out.append(_finding(key, "hash-unpinned",
                                f"{key}: no sha256 for {src}; without a pin the raw file can "
                                f"change silently under this constant. Computed sha256 = {actual}",
                                confidence="needs_review", fix=("safe", "suggest_only")))
        elif pinned.strip().lower() != actual:
            out.append(_finding(key, "hash-mismatch",
                                f"{key}: sha256 of {src} is {actual}, declared "
                                f"{pinned.strip().lower()}; either the raw data changed or the "
                                "constant is stale -- a person must decide which.",
                                fix=("unsafe", "suggest_only")))

    # P3: metadata
    inst = c.get("instrument")
    if not isinstance(inst, str) or not inst.strip() or inst.strip().upper() == "TODO":
        out.append(_finding(key, "metadata-missing",
                            f"{key}: `instrument` is missing or a TODO placeholder; a measurement with no instrument "
                            "cannot be judged for bias or range."))
    if "measured_at" not in c:
        out.append(_finding(key, "metadata-missing",
                            f"{key}: `measured_at` is missing; the age of a calibration is "
                            "part of its validity."))
    else:
        try:
            d = _parse_date(c["measured_at"])
            if d > today:
                out.append(_finding(key, "metadata-missing",
                                    f"{key}: measured_at {d} is in the future (today {today}); "
                                    "a measurement cannot postdate the declaration."))
        except ValueError:
            out.append(_finding(key, "metadata-missing",
                                f"{key}: measured_at {c['measured_at']!r} is not an ISO date "
                                "(YYYY-MM-DD); a placeholder date is no provenance."))

    # P4: fit script
    fs = c.get("fit_script")
    if fs is not None and (not isinstance(fs, str) or not (base / fs).is_file()):
        out.append(_finding(key, "script-missing",
                            f"{key}: fit_script {fs!r} does not exist; the derivation from "
                            "raw data to this constant cannot be re-run."))

    # value_ref (P6 input)
    used: float | None = None
    ref = c.get("value_ref")
    if ref is not None:
        try:
            used = resolve_value_ref(str(ref), base)
        except ValueError as e:
            out.append(_finding(key, "value-ref-unresolved",
                                f"{key}: cannot verify the value in use: {e}",
                                status="cannot_check"))

    # P5: range
    vr = c.get("valid_range")
    lo = hi = None
    if vr is not None:
        if not (isinstance(vr, list) and len(vr) == 2 and all(_num(x) for x in vr)):
            out.append(_finding(key, "range-malformed",
                                f"{key}: valid_range must be [lo, hi] of two numbers, got {vr!r}."))
        elif vr[0] > vr[1]:
            out.append(_finding(key, "range-inverted",
                                f"{key}: valid_range {vr} has lo > hi; no value can satisfy it, "
                                "so every range check on this constant is meaningless."))
        else:
            lo, hi = vr
    if lo is not None:
        for label, v in (("declared value", value if have_value else None),
                         (f"value in use ({ref})", used)):
            if v is not None and not (lo <= v <= hi):
                out.append(_finding(key, "range-violation",
                                    f"{key}: {label} {v} is outside valid_range [{lo}, {hi}]; "
                                    "the constant is being used beyond what was measured."))

    # P6: drift
    if used is not None and have_value:
        rtol = c.get("rtol", 1e-9)
        if not _num(rtol) or rtol < 0:
            rtol = 1e-9
        if not math.isclose(used, float(value), rel_tol=rtol, abs_tol=0.0):
            out.append(_finding(key, "value-drift",
                                f"{key}: value in use ({ref}) is {used}, declared/sourced value is "
                                f"{value} (rtol {rtol}); the constant being used is not the "
                                "constant that was sourced."))
    return out


def check_declaration(path: Path, today: _dt.date | None = None) -> Report:
    today = today or _dt.date.today()
    doc = load_toml(path)
    base = path.resolve().parent
    consts = doc.get("constants")
    if not isinstance(consts, dict) or not consts:
        raise CannotCheck("zero constants declared; a check that examines nothing "
                          "would pass by construction")
    expect = None
    prov = doc.get("provenance", {})
    if isinstance(prov, dict) and "expect" in prov:
        expect = prov["expect"]
        if not isinstance(expect, list) or not expect \
                or not all(isinstance(k, str) for k in expect):
            raise CannotCheck("[provenance].expect is empty or not a list of strings")

    rep = Report(checked=len(consts))
    for key, c in consts.items():
        if not isinstance(c, dict):
            rep.findings.append(_finding(key, "metadata-missing",
                                         f"{key}: declaration is not a table."))
            continue
        rep.findings.extend(_check_constant(key, c, base, today))
    for key in expect or []:
        if key not in consts:
            rep.findings.append(_finding(key, "undeclared",
                                         f"{key}: expected in [provenance].expect but has no "
                                         f"[constants.{key}]; a fitted constant with no declared "
                                         "dataset is exactly what this tool exists to refuse."))
    return rep
