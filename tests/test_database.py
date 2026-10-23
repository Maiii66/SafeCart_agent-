from database import connection, repository as repo


def test_log_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    repo.add_log("guardrail", "blocked_over_budget", "$60.00 is over the $50.00 budget", ref="demo-bud-03")
    repo.add_log("human", "approved", "approved Nimbus Air Buds")
    logs = repo.list_logs()
    assert [l["action"] for l in logs] == ["approved", "blocked_over_budget"]
    assert logs[1]["ref"] == "demo-bud-03" and logs[1]["actor"] == "guardrail"


def test_sql_is_parameterised(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "DB_PATH", tmp_path / "t.db")
    repo.add_log("agent", "x", "'); DROP TABLE action_log;--")
    assert len(repo.list_logs()) == 1
