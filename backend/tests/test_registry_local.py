from app.registry.local import LocalRegistryConnector


def test_check_health_reports_reachable_and_authenticated():
    connector = LocalRegistryConnector("local", "local")
    health = connector.check_health()
    assert health.reachable is True
    assert health.authenticated is True


def test_push_then_pull_round_trip():
    connector = LocalRegistryConnector("local", "local")
    pushed = connector.push(agent_name="Invoice Agent", definition={"name": "Invoice Agent"}, tags=["finance"])

    pulled = connector.pull(pushed.id)
    assert pulled is not None
    assert pulled.agent_name == "Invoice Agent"
    assert pulled.tags == ["finance"]


def test_pull_unknown_id_returns_none():
    connector = LocalRegistryConnector("local", "local")
    assert connector.pull("does-not-exist") is None


def test_pull_ignores_entries_from_a_different_registry_name():
    a = LocalRegistryConnector("registry-a", "local")
    b = LocalRegistryConnector("registry-b", "local")
    entry = a.push(agent_name="A-only Agent", definition={})

    assert a.pull(entry.id) is not None
    assert b.pull(entry.id) is None


def test_search_matches_name_or_tag_case_insensitively():
    connector = LocalRegistryConnector("local", "local")
    connector.push(agent_name="Invoice Fetcher Agent", definition={}, tags=["finance", "ERP"])
    connector.push(agent_name="Ticket Router Agent", definition={}, tags=["support"])

    by_name = connector.search("invoice")
    assert [e.agent_name for e in by_name] == ["Invoice Fetcher Agent"]

    by_tag = connector.search("erp")
    assert [e.agent_name for e in by_tag] == ["Invoice Fetcher Agent"]

    browse_all = connector.search("")
    assert {e.agent_name for e in browse_all} == {"Invoice Fetcher Agent", "Ticket Router Agent"}


def test_search_only_within_its_own_registry_name():
    a = LocalRegistryConnector("registry-a", "local")
    b = LocalRegistryConnector("registry-b", "local")
    a.push(agent_name="Shared Name Agent", definition={})

    assert len(a.search("")) == 1
    assert len(b.search("")) == 0
