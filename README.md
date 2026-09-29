# provenance-check

Check that a derived constant really came from the dataset it claims — source
file, hash, measurement date, instrument, valid range, and the value the code
actually uses — and **refuse the claim** when any of it does not hold.

```
$ provenance-check check
[value-drift] violation (certain) k_17
    k_17: value in use (params/model.json:kinetics.k_17) is 0.13, declared/sourced value is 0.1077 (rtol 1e-09); the constant being used is not the constant that was sourced.
[undeclared] violation (certain) kla_scale
    kla_scale: expected in [provenance].expect but has no [constants.kla_scale]; a fitted constant with no declared dataset is exactly what this tool exists to refuse.
provenance-check: 2 violation(s), 0 cannot-check across 1 declared constant(s)
```

(exit code 1; output above is from a real run on a two-file toy project.)

## The failure it exists for

A calibration object accepts a pasted slope and intercept, defaults `r2=1.0`,
and happily extrapolates outside the range it was ever measured over. The
constant wears a perfect R², and nothing anywhere says which run produced it,
on what instrument, or whether the raw file still exists. Every number in the
model is *plausible*; none is *sourced*.

The same shape has a second form, the one that makes a check worthless: it
passes because it checked nothing. A declaration with zero constants, an
`expect` list that is empty, a `value_ref` that points at a key that is gone —
each would print "OK" if the tool only reported what it found. Here each one
is **exit 2, cannot check**, never exit 0.

## The declaration

`provenance.toml`, paths relative to the file:

```toml
[provenance]
expect = ["k_17", "kla_scale"]        # keys that MUST be declared

[constants.k_17]
value = 0.1077
unit = "1/min"
source = "rawdata/run_0502.csv"       # required
sha256 = "<hex>"                      # `provenance-check hash <file>`
measured_at = "2026-05-02"            # required, ISO date, not in the future
instrument = "HPLC-RID"               # required
fit_script = "scripts/fit_k17.py"     # optional, must exist
valid_range = [0.05, 0.5]             # optional
value_ref = "params/model.json:kinetics.k_17"   # where the value is USED (json/toml)
rtol = 1e-9
```

## Rules

| rule | fires when | verdict |
|---|---|---|
| `source-missing` | source file absent | violation, certain |
| `hash-mismatch` | sha256 differs from the file | violation, certain; fix unsafe/suggest_only |
| `hash-unpinned` | no sha256 | violation, needs_review; fix safe/suggest_only (hash in message) |
| `metadata-missing` | `measured_at`/`instrument` absent, `TODO`, unparseable, or date in the future | violation, certain |
| `script-missing` | `fit_script` absent | violation, certain |
| `range-violation` / `range-inverted` | declared or in-use value outside `valid_range` / lo > hi | violation, certain |
| `value-drift` | value at `value_ref` differs from the declared value beyond `rtol` | violation, certain |
| `value-ref-unresolved` | `value_ref` file/key missing | cannot_check (exit 2, unless a violation also exists: exit 1) |
| `undeclared` | key in `expect` has no `[constants.<key>]` | violation, certain |

Findings use the same schema as [fiducial](https://github.com/jahyunlee00299/fiducial)
(`status`, `confidence`, `rule`, `message`, optional `fix`), so a caller that
reads one reads both.

## Commands

```
provenance-check check [provenance.toml ...] [--format text|json]
provenance-check hash <file>
provenance-check init --params <json|toml> --source <raw file> [--out provenance.toml] [--instrument X]
```

`init` drafts a declaration from a params file's numeric leaves (dotted keys),
with the source hash filled in and `measured_at = "TODO"`. The untouched draft
**fails** `check` on purpose — a draft must never pass silently. It refuses to
overwrite an existing file:

```
$ provenance-check init --params params/model.json --source rawdata/run_0502.csv --out draft.toml
$ provenance-check check draft.toml
[metadata-missing] violation (certain) kinetics.k_17
    kinetics.k_17: measured_at 'TODO' is not an ISO date (YYYY-MM-DD); a placeholder date is no provenance.
provenance-check: 1 violation(s), 0 cannot-check across 1 declared constant(s)
```

Exit codes, shared with the family: `0` clean, `1` violations, `2` cannot check
(reason on stderr as `provenance-check: cannot check — <reason>`).

## How it composes

- **fiducial** reads the code: `fiducial coverage` flags a declared-but-untested
  key; `provenance-check` flags a constant that has no dataset. Neither imports
  the other. See fiducial's `docs/FOUR_AXES.md` (axis ②).
- **Raw-data housekeeping** (`check_raw.py`-style: is the raw file tracked in
  git, is it duplicated) is a separate concern. **This tool does NOT check
  git-tracking of raw data** — only that the file exists and matches its hash.
- Freshness by mtime is deliberately not used (unreliable on synced folders);
  the hash is the only identity.

Pre-commit: `.pre-commit-hooks.yaml` provides the `provenance-check` hook on
`provenance.toml`.

## Install

Not published — `pip install .` into a venv (stdlib only, Python >= 3.11).
