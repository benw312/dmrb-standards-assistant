from core.smoke_test import CheckResult, run_checks, summarise


def test_passing_check_reports_its_detail():
    results = run_checks([("chat", lambda: "replied 'ready'")])

    assert results == [CheckResult("chat", True, "replied 'ready'")]


def test_failing_check_is_reported_not_raised():
    def boom() -> str:
        raise PermissionError("403 Forbidden\nlong body here")

    results = run_checks([("search", boom), ("chat", lambda: "ok")])

    assert results[0] == CheckResult("search", False, "PermissionError: 403 Forbidden")
    assert results[1].ok  # later checks still run


def test_long_errors_are_truncated():
    def boom() -> str:
        raise RuntimeError("x" * 1000)

    assert len(run_checks([("di", boom)])[0].detail) < 320


def test_summary_exit_code_reflects_any_failure():
    ok = [CheckResult("a", True, "fine")]
    bad = ok + [CheckResult("b", False, "Error: no")]

    assert summarise(ok)[1] == 0
    text, code = summarise(bad)
    assert code == 1
    assert "FAIL  b" in text and "PASS  a" in text


def test_endpoint_hosts_are_redacted_from_errors():
    def boom() -> str:
        raise RuntimeError("404 for https://srch-x.search.windows.net/indexes")

    result = run_checks([("search", boom)], redact=["srch-x.search.windows.net"])[0]

    assert "srch-x" not in result.detail
    assert "https://<endpoint>/indexes" in result.detail
