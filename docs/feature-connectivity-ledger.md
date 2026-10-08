# Feature connectivity ledger

One entry per rule. Evidence = the test that proves the clean path; refutation
= the test that makes the rule fire on a tailored fixture (all in
`tests/test_provenance.py`). All 31 tests pass.

| rule | scope | inputs -> outputs | evidence | refutation result | deferred risk |
|---|---|---|---|---|---|
| P1 `source-missing` | each constant | `source` path -> violation/certain, no fix | `test_clean_exit0` | `test_p1_source_missing`: file deleted -> exit 1, no `fix` key | a directory or symlink target counts as missing; unreadable-but-present file raises OSError (uncaught) |
| P2 `hash-mismatch` | each constant with sha256 | file bytes -> violation/certain, fix unsafe/suggest_only | `test_clean_exit0` | `test_p2_hash_mismatch`: file appended -> fires with fix grade | whole-file hashing only; line-ending normalisation (CRLF) changes the hash by design |
| P2 `hash-unpinned` | each constant lacking sha256 | -> violation/needs_review, fix safe/suggest_only, hash in message | - | `test_p2_hash_unpinned` | no `--apply` to write the pin; `suggest_only` today |
| P3 `metadata-missing` | `value`, `measured_at`, `instrument` | absent/`TODO`/unparseable/future -> violation/certain | clean fixture | `test_p3_*` (missing each, TODO date, future date, TODO instrument) | only the date is validated, not that it plausibly matches the file's contents |
| P4 `script-missing` | constants with `fit_script` | path -> violation/certain | clean fixture | `test_p4_script_missing` | existence only; script is not run or hashed |
| P5 `range-violation` / `range-inverted` / `range-malformed` | constants with `valid_range` | declared and in-use value vs `[lo, hi]` | clean fixture | `test_p5_declared_*`, `test_p5_used_value_out_of_range_only`, `test_p5_range_inverted` | range is the constant's own range, not the calibrated *input* domain of a curve |
| P6 `value-drift` | constants with `value_ref` | json/toml lookup, `math.isclose(rel_tol=rtol)` -> violation/certain | `test_p6_value_ref_toml`, `test_p6_rtol_admits_small_difference` | `test_p6_value_drift_in_range` | dotted keys with literal dots in a key name are not addressable; array indexing unsupported |
| P6 `value-ref-unresolved` | same | missing file/key/non-number -> cannot_check | - | `test_p6_value_ref_*_exit2`, `test_violation_beats_cannot_check` | none known |
| P7 `undeclared` | `[provenance].expect` | key set difference -> violation/certain, no fix | clean fixture | `test_p7_undeclared` | `expect` is hand-maintained; `init` seeds it but nothing detects a new params leaf |
| blind-pass refusal | file level | zero constants / empty `expect` / bad TOML / missing file -> exit 2 | - | `test_zero_constants_exit2`, `test_empty_expect_exit2`, `test_unparseable_toml_exit2`, `test_missing_declaration_exit2` | none known |
| `init` / `hash` | CLI | params + raw -> DRAFT that fails check; sha256 | `test_init_completed_draft_passes`, `test_hash_helper_equals_hashlib` | `test_init_draft_fails_check`, `test_init_refuses_overwrite` | non-finite/bool/string leaves are skipped silently |
| P8 `orphan-source-fresh` | - | - | - | - | skipped by spec: mtime is banned on OneDrive-synced data |
| update notice (CLI) | `cli.main` after the command | PyPI JSON -> one stderr line, TTY only, cached 24 h incl. failures | `tests/test_update_notice.py` | `test_future_timestamp_cache_is_not_fresh`, `test_garbage_cache_is_ignored_not_raised`, `test_main_rc_unchanged_when_notify_raises` (found an unguarded call site) | no test starts the real CLI as a subprocess under a pseudo-terminal; cache dir name `verification-gates` is shared by sibling tools |
