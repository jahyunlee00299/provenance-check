"""cli.main must return the command's exit code even when the update notice misbehaves."""
from provenance_check import _update_notice, cli


def test_main_rc_unchanged_when_notify_raises(tmp_path, monkeypatch):
    called = []

    def boom(*a, **k):
        called.append(a)
        raise RuntimeError("notice exploded")

    monkeypatch.setattr(_update_notice, "notify", boom)
    monkeypatch.setattr(cli, "_run", lambda argv=None: 7)
    try:
        rc = cli.main([])
    except RuntimeError:
        rc = "raised"
    # documents the current contract: notify is called after the command; a raising notify must not
    # replace the exit code. notify itself swallows errors, so this guards the call site if that changes.
    assert called, "notify was never called from main"
    assert rc == 7
