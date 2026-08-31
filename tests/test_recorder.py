"""完成 PTT 录音、降级和目录 rotate 测试。"""

import copy
import logging
import os
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import pytest

import fmo_repeater.service.recorder as recorder_module
from fmo_repeater.codecs import (
    OpusEncoder,
    RadpcmDecoder,
    RadpcmEncoder,
    opus_is_available,
)
from fmo_repeater.protocol import (
    COMPRESS_OPUS,
    COMPRESS_RADPCM,
    EncodedVoiceFrame,
    PacketBuilder,
    PacketParser,
)
from fmo_repeater.service import Recorder, TimedPacket, TransmissionCompleted


class MemoryEventLog:
    def __init__(self):
        self.events = []

    def log(self, event, **fields):
        self.events.append({'event': event, **fields})


def recording_config(service_config, directory):
    config = copy.deepcopy(service_config)
    config['recording'] = {
        'enabled': True,
        'directory': str(directory),
        'max_total_size': 0,
        'max_age': 0,
    }
    return config


def transmission(payloads, wall_time=1700000000.125, callsign='FMOTEST', uid=42):
    return TransmissionCompleted(
        vendor=0x1111,
        uid=uid,
        callsign=callsign,
        stream_begin_utc=1000,
        first_received_at=10.0,
        last_received_at=10.1,
        first_received_wall_time=wall_time,
        packets=tuple(TimedPacket(payload, i * 0.1) for i, payload in enumerate(payloads)),
        reason='idle_timeout',
    )


def build_packet(frames):
    builder = PacketBuilder(vendor=0x1111, uid=42, callsign='FMOTEST')
    packets = []
    for frame in frames:
        packet = builder.add_frame(frame)
        if packet is not None:
            packets.append(packet)
    tail = builder.flush()
    if tail is not None:
        packets.append(tail)
    return packets


def event_named(event_log, name):
    return [event for event in event_log.events if event['event'] == name]


def test_disabled_is_noop_without_directory(service_config, tmp_path, make_packet):
    directory = tmp_path / 'absent'
    config = copy.deepcopy(service_config)
    config['recording'].update(enabled=False, directory=str(directory))
    recorder = Recorder(config, event_log=MemoryEventLog())
    recorder.handle(transmission([make_packet()]))
    assert not directory.exists()


def test_radpcm_ptt_writes_one_flat_wav(
    service_config, tmp_path, make_packet
):
    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)
    payload = make_packet(uid=123, callsign='FM O/1', n_frames=2)
    event = transmission(
        [payload], wall_time=1700000000.125, callsign='FM O/1', uid=123
    )

    recorder.handle(event)

    files = list(directory.iterdir())
    assert len(files) == 1
    local = datetime.fromtimestamp(1700000000.125)
    expected_prefix = local.strftime('%Y%m%d-%H%M%S') + '-125-FM_O_1-123'
    assert files[0].name == expected_prefix + '.wav'
    with wave.open(str(files[0]), 'rb') as audio:
        assert audio.getparams()[:3] == (1, 2, 8000)
        pcm = audio.readframes(audio.getnframes())
        assert audio.getnframes() == 1280

    decoder = RadpcmDecoder()
    frames = PacketParser.parse(payload).frames
    expected_pcm = b''.join(decoder.decode(frame.encoded.raw) for frame in frames)
    assert pcm == expected_pcm
    saved = event_named(events, 'recording_saved')[0]
    assert saved['codec'] == 'RADPCM'
    assert saved['frames'] == 2
    assert saved['duration_ms'] == 160
    assert saved['bytes'] == files[0].stat().st_size
    assert 'degraded' not in saved


def test_opus_unavailable_saves_complete_opusraw(
    service_config, tmp_path, make_packet, monkeypatch
):
    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)
    payload = make_packet(codec=COMPRESS_OPUS, n_frames=2)
    monkeypatch.setattr(recorder_module, 'opus_is_available', lambda: False)

    recorder.handle(transmission([payload]))

    target = next(directory.glob('*.opusraw'))
    frames = PacketParser.parse(payload).frames
    assert target.read_bytes() == b''.join(f.encoded.to_bytes() for f in frames)
    saved = event_named(events, 'recording_saved')[0]
    assert saved['codec'] == 'OPUS'
    assert saved['degraded'] is True


@pytest.mark.skipif(not opus_is_available(), reason='opuslib/libopus 不可用')
def test_opus_ptt_writes_one_wav(service_config, tmp_path, pcm_sine_640):
    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)
    encoder = OpusEncoder()
    encoded = [
        EncodedVoiceFrame(COMPRESS_OPUS, encoder.encode(pcm_sine_640[:640])),
        EncodedVoiceFrame(COMPRESS_OPUS, encoder.encode(pcm_sine_640[640:])),
    ]

    recorder.handle(transmission(build_packet(encoded)))

    target = next(directory.glob('*.wav'))
    with wave.open(str(target), 'rb') as audio:
        assert audio.getnframes() == 640
    saved = event_named(events, 'recording_saved')[0]
    assert saved['codec'] == 'OPUS'
    assert saved['duration_ms'] == 80


def test_mixed_codec_is_logged_and_falls_back_to_one_fmoraw(
    service_config, tmp_path, pcm_sine_640, monkeypatch, caplog
):
    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(
        recording_config(service_config, directory),
        events,
        logger=logging.getLogger('test-recorder-mixed'),
    )
    radpcm = RadpcmEncoder().encode(pcm_sine_640)
    encoded = [
        EncodedVoiceFrame(COMPRESS_RADPCM, radpcm),
        EncodedVoiceFrame(COMPRESS_OPUS, b'opus-data'),
    ]
    monkeypatch.setattr(recorder_module, 'opus_is_available', lambda: False)

    with caplog.at_level(logging.WARNING):
        recorder.handle(transmission(build_packet(encoded)))

    target = next(directory.glob('*.fmoraw'))
    assert target.read_bytes() == b''.join(frame.to_bytes() for frame in encoded)
    assert len(event_named(events, 'recording_codec_changed')) == 1
    assert '编码发生变化' in caplog.text
    saved = event_named(events, 'recording_saved')[0]
    assert saved['codec'] == 'MIXED'
    assert saved['degraded'] is True


def test_mixed_codec_decodes_to_one_wav_when_opus_available(
    service_config, tmp_path, pcm_sine_640, monkeypatch
):
    class FakeOpusDecoder:
        def decode(self, data):
            return b'\x02\x00' * 320

    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)
    radpcm = RadpcmEncoder().encode(pcm_sine_640)
    encoded = [
        EncodedVoiceFrame(COMPRESS_RADPCM, radpcm),
        EncodedVoiceFrame(COMPRESS_OPUS, b'opus-data'),
    ]
    monkeypatch.setattr(recorder_module, 'opus_is_available', lambda: True)
    monkeypatch.setattr(recorder_module, 'OpusDecoder', FakeOpusDecoder)

    recorder.handle(transmission(build_packet(encoded)))

    target = next(directory.glob('*.wav'))
    with wave.open(str(target), 'rb') as audio:
        assert audio.getnframes() == 960
    saved = event_named(events, 'recording_saved')[0]
    assert saved['codec'] == 'MIXED'
    assert saved['frames'] == 2


def test_all_decode_failures_discard_recording(service_config, tmp_path):
    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)
    packets = build_packet([EncodedVoiceFrame(COMPRESS_RADPCM, b'bad')])

    recorder.handle(transmission(packets))

    assert not list(directory.glob('*.wav'))
    assert event_named(events, 'recording_discarded')[0]['reason'] == 'decode_error'


def test_atomic_write_failure_is_discarded(
    service_config, tmp_path, make_packet, monkeypatch
):
    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)

    def fail_replace(source, target):
        raise OSError('disk failure')

    monkeypatch.setattr(recorder_module.os, 'replace', fail_replace)
    recorder.handle(transmission([make_packet()]))
    assert not list(directory.glob('*.wav'))
    assert not list(directory.glob('*.tmp'))
    assert event_named(events, 'recording_discarded')[0]['reason'] == 'io_error'


def test_stop_cancels_inflight_decode(
    service_config, tmp_path, make_packet, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()

    class BlockingDecoder:
        def decode(self, data):
            entered.set()
            assert release.wait(1.0)
            return b'\x00\x00' * 640

    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)
    monkeypatch.setattr(recorder_module, 'RadpcmDecoder', BlockingDecoder)
    worker = threading.Thread(
        target=recorder.handle,
        args=(transmission([make_packet()]),),
    )
    worker.start()
    assert entered.wait(1.0)

    recorder.stop()
    release.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert not list(directory.glob('*.wav'))
    assert event_named(events, 'recording_discarded')[0]['reason'] == 'shutdown'


def test_stop_during_raw_write_removes_temporary_file(
    service_config, tmp_path, make_packet, monkeypatch
):
    directory = tmp_path / 'recording'
    events = MemoryEventLog()
    recorder = Recorder(recording_config(service_config, directory), events)
    monkeypatch.setattr(recorder_module, 'opus_is_available', lambda: False)

    class InterruptingFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def write(self, data):
            recorder.stop()
            return len(data)

    monkeypatch.setattr(
        recorder_module,
        'open',
        lambda path, mode: InterruptingFile(),
        raising=False,
    )
    recorder.handle(transmission([make_packet(codec=COMPRESS_OPUS)]))

    assert not list(directory.glob('*.tmp'))
    assert not list(directory.glob('*.opusraw'))
    assert event_named(events, 'recording_discarded')[0]['reason'] == 'shutdown'


def test_cleanup_removes_expired_and_preserves_other_files(service_config, tmp_path):
    directory = tmp_path / 'recording'
    directory.mkdir()
    expired = directory / 'expired.wav'
    current = directory / 'current.opusraw'
    unrelated = directory / 'notes.txt'
    expired.write_bytes(b'old')
    current.write_bytes(b'new')
    unrelated.write_bytes(b'keep')
    now = time.time()
    os.utime(expired, (now - 7200, now - 7200))
    config = recording_config(service_config, directory)
    config['recording']['max_age'] = '1h'
    events = MemoryEventLog()

    Recorder(config, events)

    assert not expired.exists()
    assert current.exists()
    assert unrelated.exists()
    assert event_named(events, 'recording_deleted')[0]['reason'] == 'age_limit'


def test_cleanup_enforces_total_size_oldest_first(service_config, tmp_path):
    directory = tmp_path / 'recording'
    directory.mkdir()
    old = directory / 'old.wav'
    new = directory / 'new.fmoraw'
    old.write_bytes(b'12345678')
    new.write_bytes(b'abcdefgh')
    now = time.time()
    os.utime(old, (now - 10, now - 10))
    config = recording_config(service_config, directory)
    config['recording']['max_total_size'] = '10B'
    events = MemoryEventLog()

    Recorder(config, events)

    assert not old.exists()
    assert new.exists()
    assert event_named(events, 'recording_deleted')[0]['reason'] == 'size_limit'


def test_saved_file_is_rotated_immediately_when_over_limit(
    service_config, tmp_path, make_packet
):
    directory = tmp_path / 'recording'
    config = recording_config(service_config, directory)
    config['recording']['max_total_size'] = '1B'
    events = MemoryEventLog()
    recorder = Recorder(config, events)

    recorder.handle(transmission([make_packet()]))

    assert not list(directory.glob('*.wav'))
    names = [event['event'] for event in events.events]
    assert names.index('recording_saved') < names.index('recording_deleted')


def test_cleanup_failure_does_not_stop_other_deletions(
    service_config, tmp_path, monkeypatch
):
    directory = tmp_path / 'recording'
    directory.mkdir()
    first = directory / 'a.wav'
    second = directory / 'b.wav'
    first.write_bytes(b'12345678')
    second.write_bytes(b'abcdefgh')
    now = time.time()
    os.utime(first, (now - 20, now - 20))
    os.utime(second, (now - 10, now - 10))
    config = recording_config(service_config, directory)
    config['recording']['max_total_size'] = '1B'
    events = MemoryEventLog()
    original_unlink = Path.unlink

    def selective_unlink(path, *args, **kwargs):
        if path.name == 'a.wav':
            raise OSError('busy')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', selective_unlink)
    Recorder(config, events)

    assert first.exists()
    assert not second.exists()
    assert event_named(events, 'recording_cleanup_failed')
    assert event_named(events, 'recording_deleted')
