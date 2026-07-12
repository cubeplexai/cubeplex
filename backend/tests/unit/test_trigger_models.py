from cubeplex.models.trigger import Trigger, TriggerEvent


def test_trigger_id_prefix_and_defaults() -> None:
    t = Trigger(
        org_id="org-x",
        workspace_id="ws-x",
        name="t",
        source_type="webhook",
        target_type="inline",
        run_as_user_id="usr-x",
        current_secret_cred_id="cred-x",
    )
    assert t.id.startswith("trig-")
    assert t.enabled is True
    assert t.conversation_policy == "new_each_time"


def test_trigger_event_id_prefix() -> None:
    e = TriggerEvent(
        org_id="org-x",
        workspace_id="ws-x",
        trigger_id="trig-x",
        source_type="webhook",
        dedup_key="abc",
        status="accepted",
    )
    assert e.id.startswith("trev-")
