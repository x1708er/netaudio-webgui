from netaudio_webgui.fixtures import DemoClient


def test_demo_state_has_devices_and_channels():
    client = DemoClient()
    state = client.get_state()
    assert len(state["devices"]) >= 2
    assert any(d["tx_channels"] for d in state["devices"])
    assert any(d["rx_channels"] for d in state["devices"])


def test_demo_add_then_remove_subscription_roundtrips():
    client = DemoClient()
    client.add_subscription(tx_device="Inferno", tx_number=1, rx_device="A32", rx_number=2)
    subs = client.get_state()["subscriptions"]
    assert any(s["rx_device"] == "A32" and s["rx_channel"] == "02"
               and s["tx_device"] == "Inferno" for s in subs)

    client.remove_subscription(rx_device="A32", rx_number=2)
    subs = client.get_state()["subscriptions"]
    assert not any(s["rx_device"] == "A32" and s["rx_channel"] == "02" for s in subs)


def test_demo_rename_device():
    client = DemoClient()
    host = client.get_state()["devices"][0]["ipv4"]
    client.set_device_name(host, "Renamed")
    names = [d["name"] for d in client.get_state()["devices"]]
    assert "Renamed" in names


def test_demo_channel_rename_updates_existing_subscription():
    client = DemoClient()
    # Pre-seeded: A32 rx "01" <- Inferno "L". Rename A32 rx channel 1.
    a32 = next(d for d in client.get_state()["devices"] if d["name"] == "A32")
    client.set_channel_name(a32["ipv4"], 1, "MainIn", "rx")
    subs = client.get_state()["subscriptions"]
    assert any(s["rx_device"] == "A32" and s["rx_channel"] == "MainIn" for s in subs)
