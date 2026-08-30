"""MqttTransport 连接、发布确认与关闭测试。"""

import threading
from types import SimpleNamespace

import pytest
from paho.mqtt import client as mqtt_client

from fmo_repeater.service import MqttTransport


class FakeInfo:
    def __init__(self, rc=0, publish_on_wait=True):
        self.rc = rc
        self.published = False
        self.publish_on_wait = publish_on_wait
        self.wait_calls = 0

    def is_published(self):
        if self.rc != mqtt_client.MQTT_ERR_SUCCESS:
            raise RuntimeError("publish failed")
        return self.published

    def wait_for_publish(self, timeout=None):
        self.wait_calls += 1
        if self.publish_on_wait:
            self.published = True


class FakeClient:
    def __init__(self, info=None):
        self.info = info or FakeInfo()
        self.calls = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def username_pw_set(self, username, password):
        self.calls.append(("credentials", username, password))

    def connect(self, broker, port, keepalive):
        self.calls.append(("connect", broker, port, keepalive))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def subscribe(self, topic, qos=0):
        self.calls.append(("subscribe", topic, qos))
        return 0, 1

    def publish(self, topic, payload, qos=0):
        self.calls.append(("publish", topic, payload, qos))
        return self.info

    def disconnect(self):
        self.calls.append(("disconnect",))

    def loop_stop(self):
        self.calls.append(("loop_stop",))


def make_transport(service_config, client=None, on_payload=None):
    client = client or FakeClient()
    factory_args = []

    def factory(*args):
        factory_args.append(args)
        return client

    transport = MqttTransport(
        service_config,
        on_payload=on_payload or (lambda payload, received_at: None),
        client_factory=factory,
    )
    return transport, client, factory_args


def test_connect_configures_credentials_qos_and_callbacks(service_config):
    service_config["mqtt"].update(username="user", password="pass", qos=1)
    connected = []
    payloads = []
    client = FakeClient()
    transport = MqttTransport(
        service_config,
        on_payload=lambda payload, received_at: payloads.append(
            (payload, received_at)
        ),
        on_connected=lambda: connected.append(True),
        client_factory=lambda *args: client,
    )
    transport.connect()
    client.on_connect(client, None, None, 0, None)
    client.on_message(client, None, SimpleNamespace(payload=b"voice"))

    assert transport.connected is True
    assert ("credentials", "user", "pass") in client.calls
    assert ("subscribe", "TEST/FMO/RAW", 1) in client.calls
    assert connected == [True]
    assert payloads[0][0] == b"voice"
    assert isinstance(payloads[0][1], float)


def test_submit_waits_for_publish_completion(service_config):
    transport, client, _ = make_transport(service_config)
    transport.connect()
    ticket = transport.submit(b"voice")
    outcome = transport.wait_for_publish(ticket)
    assert outcome.success is True
    assert outcome.reason == "published"
    assert client.info.wait_calls == 1


def test_publish_rc_failure_does_not_claim_success(service_config):
    client = FakeClient(FakeInfo(rc=mqtt_client.MQTT_ERR_NO_CONN))
    transport, _, _ = make_transport(service_config, client)
    transport.connect()
    outcome = transport.wait_for_publish(transport.submit(b"voice"))
    assert outcome.success is False
    assert outcome.reason == "publish_rc"


def test_publish_timeout(service_config):
    service_config["mqtt"]["publish_timeout"] = 0.01
    client = FakeClient(FakeInfo(publish_on_wait=False))
    transport, _, _ = make_transport(service_config, client)
    transport.connect()
    outcome = transport.wait_for_publish(transport.submit(b"voice"))
    assert outcome.success is False
    assert outcome.reason == "timeout"


def test_publish_wait_can_be_cancelled(service_config):
    client = FakeClient(FakeInfo(publish_on_wait=False))
    transport, _, _ = make_transport(service_config, client)
    transport.connect()
    cancel = threading.Event()
    cancel.set()
    outcome = transport.wait_for_publish(transport.submit(b"voice"), cancel)
    assert outcome.success is False
    assert outcome.reason == "cancelled"
    assert client.info.wait_calls == 0


def test_quiesce_rejects_publish_and_ignores_inbound(service_config):
    payloads = []
    transport, client, _ = make_transport(
        service_config, on_payload=lambda *args: payloads.append(args)
    )
    transport.connect()
    transport.quiesce()
    client.on_message(client, None, SimpleNamespace(payload=b"late"))
    with pytest.raises(RuntimeError, match="停止接受"):
        transport.submit(b"late")
    assert payloads == []


def test_disconnect_happens_before_loop_stop(service_config):
    transport, client, _ = make_transport(service_config)
    transport.connect()
    transport.disconnect()
    names = [call[0] for call in client.calls]
    assert names.index("disconnect") < names.index("loop_stop")

