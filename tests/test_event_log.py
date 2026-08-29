"""JSONL 事件日志测试"""

import json

from fmo_repeater.service import EventLog


def read_events(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestEventLog:
    def test_jsonl_written(self, tmp_path):
        cfg = {
            'event_log': {
                'enabled': True,
                'file': str(tmp_path / "events.jsonl"),
                'max_bytes': 1048576,
                'backup_count': 2,
            }
        }
        evlog = EventLog(cfg)
        evlog.log("service_started", version=1, vendor=0x2000)
        evlog.log("packet_received", uid=42, callsign="BD8BOJ", frames=3)
        evlog.close()

        events = read_events(cfg['event_log']['file'])
        assert len(events) == 2
        assert events[0]["event"] == "service_started"
        assert events[0]["version"] == 1
        assert events[1]["event"] == "packet_received"
        assert events[1]["uid"] == 42
        # ts 在首位，ISO8601
        for e in events:
            assert list(e.keys())[0] == "ts"
            assert "T" in e["ts"]

    def test_disabled_noop(self, tmp_path):
        cfg = {
            'event_log': {
                'enabled': False,
                'file': str(tmp_path / "events.jsonl"),
                'max_bytes': 1048576,
                'backup_count': 2,
            }
        }
        evlog = EventLog(cfg)
        evlog.log("anything", x=1)
        assert evlog.written == 0
        assert not (tmp_path / "events.jsonl").exists()

    def test_missing_config_noop(self):
        evlog = EventLog({})
        evlog.log("anything")
        assert evlog.written == 0

    def test_directory_created(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "events.jsonl"
        cfg = {
            'event_log': {
                'enabled': True,
                'file': str(target),
                'max_bytes': 1048576,
                'backup_count': 1,
            }
        }
        evlog = EventLog(cfg)
        evlog.log("e1")
        evlog.close()
        assert target.exists()

    def test_rotation(self, tmp_path):
        target = tmp_path / "events.jsonl"
        cfg = {
            'event_log': {
                'enabled': True,
                'file': str(target),
                'max_bytes': 220,      # 约一行大小，强制快速轮转
                'backup_count': 2,
            }
        }
        evlog = EventLog(cfg)
        for i in range(30):
            evlog.log("tick", seq=i, pad="x" * 100)
        evlog.close()
        # 主文件 + 最多 2 个轮转备份
        files = sorted(p.name for p in tmp_path.iterdir())
        assert "events.jsonl" in files
        assert len(files) <= 3
        # 每个文件内的行都是合法 JSON
        for name in files:
            for e in read_events(tmp_path / name):
                assert "event" in e

    def test_unicode_fields(self, tmp_path):
        cfg = {
            'event_log': {
                'enabled': True,
                'file': str(tmp_path / "events.jsonl"),
                'max_bytes': 1048576,
                'backup_count': 1,
            }
        }
        evlog = EventLog(cfg)
        evlog.log("packet_received", callsign="乙一")
        evlog.close()
        events = read_events(cfg['event_log']['file'])
        assert events[0]["callsign"] == "乙一"
