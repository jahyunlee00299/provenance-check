import hashlib
import json
import subprocess
import sys

import pytest

from provenance_check.cli import main

RAW = b"t,area\n0,1.0\n1,2.0\n"


def digest(b=RAW):
    return hashlib.sha256(b).hexdigest()


def make(tmp_path, extra="", const_extra="", expect='["k_17"]', raw=RAW,
         with_params=True, header=None):
    """A clean, complete declaration; every knob is overridable per test."""
    (tmp_path / "rawdata").mkdir(exist_ok=True)
    (tmp_path / "rawdata" / "run.csv").write_bytes(raw)
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "fit.py").write_text("# fit\n", encoding="utf-8")
    if with_params:
        (tmp_path / "params").mkdir(exist_ok=True)
        (tmp_path / "params" / "model.json").write_text(
            json.dumps({"kinetics": {"k_17": 0.1077}}), encoding="utf-8")
    text = header if header is not None else f"[provenance]\nexpect = {expect}\n"
    text += f"""
[constants.k_17]
value = 0.1077
unit = "1/min"
source = "rawdata/run.csv"
sha256 = "{digest()}"
measured_at = "2026-05-02"
instrument = "HPLC-RID"
fit_script = "scripts/fit.py"
valid_range = [0.05, 0.5]
value_ref = "params/model.json:kinetics.k_17"
{const_extra}
{extra}
"""
    p = tmp_path / "provenance.toml"
    p.write_text(text, encoding="utf-8")
    return p


def run_json(p, capsys):
    rc = main(["check", str(p), "--format", "json"])
    return rc, json.loads(capsys.readouterr().out)


def rules(doc):
    return {f["rule"] for f in doc["findings"]}


def test_clean_exit0(tmp_path, capsys):
    rc, doc = run_json(make(tmp_path), capsys)
    assert rc == 0 and doc["findings"] == []
    assert doc["summary"] == {"checked": 1, "violations": 0, "cannot_check": 0}


def test_text_summary_line(tmp_path, capsys):
    assert main(["check", str(make(tmp_path))]) == 0
    assert "0 violation(s), 0 cannot-check across 1 declared constant(s)" in capsys.readouterr().out


def test_p1_source_missing(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "rawdata" / "run.csv").unlink()
    rc, doc = run_json(p, capsys)
    f = [x for x in doc["findings"] if x["rule"] == "source-missing"]
    assert rc == 1 and len(f) == 1 and f[0]["confidence"] == "certain" and "fix" not in f[0]


def test_p2_hash_mismatch(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "rawdata" / "run.csv").write_bytes(RAW + b"2,3.0\n")
    rc, doc = run_json(p, capsys)
    f = [x for x in doc["findings"] if x["rule"] == "hash-mismatch"]
    assert rc == 1 and f[0]["fix"] == {"applicability": "unsafe", "apply": "suggest_only"}
    assert f[0]["confidence"] == "certain"


def test_p2_hash_unpinned(tmp_path, capsys):
    p = make(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace(f'sha256 = "{digest()}"\n', ""),
                 encoding="utf-8")
    rc, doc = run_json(p, capsys)
    f = [x for x in doc["findings"] if x["rule"] == "hash-unpinned"]
    assert rc == 1 and f[0]["confidence"] == "needs_review"
    assert f[0]["fix"] == {"applicability": "safe", "apply": "suggest_only"}
    assert digest() in f[0]["message"]


@pytest.mark.parametrize("field", ["measured_at", "instrument"])
def test_p3_metadata_missing(tmp_path, capsys, field):
    p = make(tmp_path)
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if not l.startswith(field)]
    p.write_text("\n".join(lines), encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 1 and rules(doc) == {"metadata-missing"}


def test_p3_unparseable_and_future_date(tmp_path, capsys):
    for bad in ('"TODO"', '"2999-01-01"'):
        d = tmp_path / bad.strip('"')
        d.mkdir()
        p = make(d)
        p.write_text(p.read_text(encoding="utf-8").replace('"2026-05-02"', bad), encoding="utf-8")
        rc, doc = run_json(p, capsys)
        assert rc == 1 and rules(doc) == {"metadata-missing"}, bad


def test_p4_script_missing(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "scripts" / "fit.py").unlink()
    rc, doc = run_json(p, capsys)
    assert rc == 1 and rules(doc) == {"script-missing"}


def test_p5_declared_value_out_of_range(tmp_path, capsys):
    p = make(tmp_path, with_params=False)
    p.write_text(p.read_text(encoding="utf-8").replace("value = 0.1077", "value = 0.9")
                 .replace('value_ref = "params/model.json:kinetics.k_17"\n', ""),
                 encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 1 and rules(doc) == {"range-violation"}


def test_p5_used_value_out_of_range_only(tmp_path, capsys):
    p = make(tmp_path)
    # declared stays 0.1077 (in range); the model file uses 0.9 (out of range, also drift)
    (tmp_path / "params" / "model.json").write_text(
        json.dumps({"kinetics": {"k_17": 0.9}}), encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 1 and rules(doc) == {"range-violation", "value-drift"}
    assert any("in use" in f["message"] for f in doc["findings"] if f["rule"] == "range-violation")


def test_p5_range_inverted(tmp_path, capsys):
    p = make(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("[0.05, 0.5]", "[0.5, 0.05]"),
                 encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 1 and rules(doc) == {"range-inverted"}


def test_p6_value_drift_in_range(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "params" / "model.json").write_text(
        json.dumps({"kinetics": {"k_17": 0.11}}), encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 1 and rules(doc) == {"value-drift"}


def test_p6_rtol_admits_small_difference(tmp_path, capsys):
    p = make(tmp_path, const_extra="rtol = 0.01")
    (tmp_path / "params" / "model.json").write_text(
        json.dumps({"kinetics": {"k_17": 0.1078}}), encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 0, doc


def test_p6_value_ref_toml(tmp_path, capsys):
    p = make(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("model.json", "model.toml"),
                 encoding="utf-8")
    (tmp_path / "params" / "model.toml").write_text("[kinetics]\nk_17 = 0.1077\n",
                                                    encoding="utf-8")
    rc, _ = run_json(p, capsys)
    assert rc == 0


def test_p6_value_ref_key_missing_exit2(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "params" / "model.json").write_text(json.dumps({"kinetics": {}}),
                                                    encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 2 and rules(doc) == {"value-ref-unresolved"}
    assert doc["findings"][0]["status"] == "cannot_check"
    assert doc["summary"]["cannot_check"] == 1


def test_p6_value_ref_file_missing_exit2(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "params" / "model.json").unlink()
    rc, _ = run_json(p, capsys)
    assert rc == 2


def test_violation_beats_cannot_check(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "params" / "model.json").write_text(json.dumps({"kinetics": {}}),
                                                    encoding="utf-8")
    (tmp_path / "scripts" / "fit.py").unlink()
    rc, doc = run_json(p, capsys)
    assert rc == 1
    assert doc["summary"]["violations"] == 1 and doc["summary"]["cannot_check"] == 1


def test_p7_undeclared(tmp_path, capsys):
    p = make(tmp_path, expect='["k_17", "kla_scale"]')
    rc, doc = run_json(p, capsys)
    f = [x for x in doc["findings"] if x["rule"] == "undeclared"]
    assert rc == 1 and len(f) == 1 and f[0]["constant"] == "kla_scale" and "fix" not in f[0]


def test_zero_constants_exit2(tmp_path, capsys):
    p = tmp_path / "provenance.toml"
    p.write_text('[provenance]\nexpect = ["a"]\n', encoding="utf-8")
    assert main(["check", str(p)]) == 2
    assert "cannot check" in capsys.readouterr().err


def test_empty_expect_exit2(tmp_path, capsys):
    assert main(["check", str(make(tmp_path, expect="[]"))]) == 2
    assert "expect" in capsys.readouterr().err


def test_unparseable_toml_exit2(tmp_path, capsys):
    p = tmp_path / "provenance.toml"
    p.write_text("[constants.x\nvalue = = 1", encoding="utf-8")
    assert main(["check", str(p)]) == 2


def test_missing_declaration_exit2(tmp_path, capsys):
    assert main(["check", str(tmp_path / "nope.toml")]) == 2
    assert capsys.readouterr().err.startswith("provenance-check: cannot check")


def test_default_path_is_cwd(tmp_path, capsys, monkeypatch):
    make(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["check"]) == 0


def test_json_schema(tmp_path, capsys):
    p = make(tmp_path)
    (tmp_path / "scripts" / "fit.py").unlink()
    _, doc = run_json(p, capsys)
    assert set(doc) == {"findings", "summary"}
    for f in doc["findings"]:
        assert {"status", "confidence", "rule", "message"} <= set(f)
        assert f["status"] in ("violation", "cannot_check")
        assert f["confidence"] in ("certain", "needs_review")
        if "fix" in f:
            assert f["fix"]["applicability"] in ("safe", "unsafe")
            assert f["fix"]["apply"] in ("auto", "suggest_only")


def test_hash_helper_equals_hashlib(tmp_path, capsys):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc" * 100000)
    assert main(["hash", str(f)]) == 0
    assert capsys.readouterr().out.strip() == hashlib.sha256(b"abc" * 100000).hexdigest()


def test_init_draft_fails_check(tmp_path, capsys):
    (tmp_path / "params.json").write_text(
        json.dumps({"kinetics": {"k_17": 0.1077, "name": "x"}, "eta": 2, "flag": True}),
        encoding="utf-8")
    (tmp_path / "raw.csv").write_bytes(RAW)
    out = tmp_path / "provenance.toml"
    assert main(["init", "--params", str(tmp_path / "params.json"),
                 "--source", str(tmp_path / "raw.csv"), "--out", str(out)]) == 0
    capsys.readouterr()
    text = out.read_text(encoding="utf-8")
    assert "kinetics.k_17" in text and "eta" in text and "flag" not in text
    rc, doc = run_json(out, capsys)
    assert rc == 1
    assert rules(doc) == {"metadata-missing"}
    assert doc["summary"]["checked"] == 2


def test_init_refuses_overwrite(tmp_path, capsys):
    (tmp_path / "params.json").write_text('{"a": 1.5}', encoding="utf-8")
    (tmp_path / "raw.csv").write_bytes(RAW)
    out = tmp_path / "provenance.toml"
    out.write_text("keep me", encoding="utf-8")
    rc = main(["init", "--params", str(tmp_path / "params.json"),
               "--source", str(tmp_path / "raw.csv"), "--out", str(out)])
    assert rc == 2 and out.read_text(encoding="utf-8") == "keep me"


def test_init_completed_draft_passes(tmp_path, capsys):
    (tmp_path / "params.json").write_text('{"a": 1.5}', encoding="utf-8")
    (tmp_path / "raw.csv").write_bytes(RAW)
    out = tmp_path / "provenance.toml"
    main(["init", "--params", str(tmp_path / "params.json"),
          "--source", str(tmp_path / "raw.csv"), "--out", str(out), "--instrument", "UV"])
    out.write_text(out.read_text(encoding="utf-8").replace('"TODO"', '"2026-05-02"'),
                   encoding="utf-8")
    capsys.readouterr()
    assert main(["check", str(out)]) == 0


def test_python_dash_m(tmp_path):
    p = make(tmp_path)
    r = subprocess.run([sys.executable, "-m", "provenance_check", "check", str(p)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


def test_p3_instrument_todo_placeholder(tmp_path, capsys):
    p = make(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace('"HPLC-RID"', '"TODO"'), encoding="utf-8")
    rc, doc = run_json(p, capsys)
    assert rc == 1 and rules(doc) == {"metadata-missing"}
