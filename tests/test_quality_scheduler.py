import data_pipeline.quality.scheduler as sch


def test_prioritize_carteira_first_then_never_then_oldest():
    universe = ["AAA3", "BBB3", "CCC3", "DDD3"]
    carteira = {"CCC3"}
    last_audited = {"AAA3": 1000.0, "BBB3": 50.0}  # DDD3 nunca auditada
    out = sch.prioritize(universe, carteira, last_audited)
    # CCC3 (carteira) primeiro; depois DDD3 (nunca); depois BBB3 (mais antiga) ; AAA3
    assert out[0] == "CCC3"
    assert out[1] == "DDD3"
    assert out.index("BBB3") < out.index("AAA3")


def test_rotate_wraps_around_for_new_cycle():
    ordered = ["A", "B", "C", "D", "E"]
    batch, cur = sch.rotate(ordered, cursor=0, n=2)
    assert batch == ["A", "B"] and cur == 2
    batch, cur = sch.rotate(ordered, cursor=4, n=2)  # passa do fim → wrap
    assert batch == ["E", "A"] and cur == 1


def test_rotate_empty():
    assert sch.rotate([], 0, 5) == ([], 0)


def test_jitter_seconds_nonnegative_and_around_base():
    vals = [sch.jitter_seconds(2.0, 0.5) for _ in range(50)]
    assert all(v >= 0 for v in vals)
    assert min(vals) >= 1.0 - 1e-9 and max(vals) <= 3.0 + 1e-9


def test_with_backoff_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(sch.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert sch.with_backoff(flaky, retries=5, base=0.01) == "ok"
    assert calls["n"] == 3


def test_with_backoff_raises_after_exhaustion(monkeypatch):
    monkeypatch.setattr(sch.time, "sleep", lambda *_: None)

    def always_fail():
        raise ValueError("nope")

    try:
        sch.with_backoff(always_fail, retries=2, base=0.01)
        assert False, "deveria ter levantado"
    except ValueError:
        pass
