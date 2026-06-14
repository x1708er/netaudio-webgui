from netaudio_webgui.app import plan_apply


def _state(subscriptions=None):
    return {
        "devices": [
            {"name": "Inferno",
             "tx_channels": [{"number": 1, "name": "L", "label": "L"},
                             {"number": 2, "name": "R", "label": "R"}],
             "rx_channels": [{"number": 1, "name": "01", "label": "01"},
                             {"number": 2, "name": "02", "label": "02"}]},
            {"name": "A32",
             "tx_channels": [{"number": 1, "name": "Mic1", "label": "Mic1"}],
             "rx_channels": [{"number": 1, "name": "01", "label": "01"},
                             {"number": 2, "name": "02", "label": "02"}]},
        ],
        "subscriptions": subscriptions or [],
    }


def test_plan_apply_adds_missing():
    desired = [{"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}]
    add, remove, skipped = plan_apply(desired, _state())
    assert add == [{"tx_device": "Inferno", "tx_number": 1, "rx_device": "A32", "rx_number": 1}]
    assert remove == []
    assert skipped == 0


def test_plan_apply_removes_extra():
    current = [{"rx_device": "A32", "rx_channel": "02", "tx_device": "Inferno", "tx_channel": "R"}]
    add, remove, skipped = plan_apply([], _state(current))
    assert add == []
    assert remove == [{"rx_device": "A32", "rx_number": 2}]
    assert skipped == 0


def test_plan_apply_no_op_for_already_present():
    sub = {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    add, remove, skipped = plan_apply([sub], _state([sub]))
    assert add == []
    assert remove == []
    assert skipped == 0


def test_plan_apply_exact_match_adds_and_removes():
    # Desired: A32/01 <- Inferno/L. Current: A32/02 <- Inferno/R.
    desired = [{"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}]
    current = [{"rx_device": "A32", "rx_channel": "02", "tx_device": "Inferno", "tx_channel": "R"}]
    add, remove, skipped = plan_apply(desired, _state(current))
    assert add == [{"tx_device": "Inferno", "tx_number": 1, "rx_device": "A32", "rx_number": 1}]
    assert remove == [{"rx_device": "A32", "rx_number": 2}]
    assert skipped == 0


def test_plan_apply_skips_unresolvable_device():
    desired = [{"rx_device": "Ghost", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}]
    add, remove, skipped = plan_apply(desired, _state())
    assert add == []
    assert remove == []
    assert skipped == 1


def test_plan_apply_skips_unresolvable_channel():
    desired = [{"rx_device": "A32", "rx_channel": "99", "tx_device": "Inferno", "tx_channel": "L"}]
    add, remove, skipped = plan_apply(desired, _state())
    assert skipped == 1
    assert add == []


def test_plan_apply_changing_tx_is_an_add():
    # RX already subscribed to Inferno/L; desired wants the same RX from Inferno/R.
    current = [{"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}]
    desired = [{"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "R"}]
    add, remove, skipped = plan_apply(desired, _state(current))
    assert add == [{"tx_device": "Inferno", "tx_number": 2, "rx_device": "A32", "rx_number": 1}]
    # The old (L) sub differs by key, so it would be removed too (Dante overwrites
    # on add, but we report both for an exact diff).
    assert remove == [{"rx_device": "A32", "rx_number": 1}]
    assert skipped == 0
