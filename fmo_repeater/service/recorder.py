"""完成 PTT 的语音录制、降级保存与目录保留策略。"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..codecs import OpusDecoder, RadpcmDecoder, opus_is_available
from ..protocol import (
    COMPRESS_OPUS,
    COMPRESS_PCM,
    COMPRESS_RADPCM,
    EncodedVoiceFrame,
    PacketParser,
    ProtocolError,
)
from .config import parse_duration, parse_size
from .event_log import EventLog
from .transmission import TransmissionCompleted


_AUDIO_SUFFIXES = {'.wav', '.opusraw', '.fmoraw'}
_CODEC_NAMES = {
    COMPRESS_PCM: 'PCM',
    COMPRESS_OPUS: 'OPUS',
    COMPRESS_RADPCM: 'RADPCM',
}


class _RecordingCancelled(Exception):
    """服务停机时中止尚未原子落盘的录音。"""


class Recorder:
    """将每个完成的 PTT 保存为一个录音文件。"""

    def __init__(
        self,
        config: Dict[str, Any],
        event_log: Optional[EventLog] = None,
        logger: Optional[logging.Logger] = None,
    ):
        cfg = config.get('recording', {})
        self.enabled = bool(cfg.get('enabled', False))
        self.event_log = event_log or EventLog(config)
        self.logger = logger or logging.getLogger('FMORepeater')
        self.directory: Optional[Path] = None
        self.max_total_bytes = 0
        self.max_age_seconds = 0
        self._stop = threading.Event()

        if not self.enabled:
            return

        self.directory = Path(cfg['directory'])
        self.max_total_bytes = parse_size(cfg['max_total_size'])
        self.max_age_seconds = parse_duration(cfg['max_age'])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cleanup()

    def stop(self) -> None:
        """请求立即停止在途录音和清理工作。"""
        self._stop.set()

    def handle(self, transmission: TransmissionCompleted) -> None:
        """录制一个完成 PTT；所有预期错误均在消费者内部隔离。"""
        if not self.enabled or self._stop.is_set():
            return

        started_at = datetime.fromtimestamp(
            transmission.first_received_wall_time
        ).astimezone().isoformat(timespec='milliseconds')
        self.event_log.log(
            'recording_stream_start',
            uid=transmission.uid,
            callsign=transmission.callsign,
            stream_begin_utc=transmission.stream_begin_utc,
            started_at=started_at,
        )

        try:
            frames = self._collect_frames(transmission)
            if not frames:
                self._discard(transmission, 'empty_stream')
                return
            modes = self._report_codec_changes(transmission, frames)
            if modes == {COMPRESS_PCM}:
                self.logger.warning(
                    "录音丢弃：PTT 使用不支持的 PCM 网络编码 uid=%s callsign=%s",
                    transmission.uid,
                    transmission.callsign,
                )
                self._discard(transmission, 'unsupported_codec')
                return

            if COMPRESS_OPUS in modes and not opus_is_available():
                suffix = '.opusraw' if modes == {COMPRESS_OPUS} else '.fmoraw'
                codec = 'OPUS' if modes == {COMPRESS_OPUS} else 'MIXED'
                self._save_raw(transmission, frames, suffix, codec)
                return

            self._save_wav(transmission, frames, modes)
        except _RecordingCancelled:
            self._discard(transmission, 'shutdown')
        except ProtocolError as exc:
            self.logger.warning(
                "录音包解析失败 uid=%s callsign=%s: %s",
                transmission.uid,
                transmission.callsign,
                exc,
            )
            self._discard(transmission, 'invalid_packet')
        except Exception as exc:
            self.logger.warning(
                "录音处理失败 uid=%s callsign=%s: %s",
                transmission.uid,
                transmission.callsign,
                exc,
                exc_info=True,
            )
            self._discard(transmission, 'recording_error')

    def _collect_frames(
        self, transmission: TransmissionCompleted
    ) -> List[EncodedVoiceFrame]:
        frames: List[EncodedVoiceFrame] = []
        for timed_packet in transmission.packets:
            self._raise_if_stopped()
            packet = PacketParser.parse(timed_packet.payload)
            frames.extend(frame.encoded for frame in packet.frames)
        return frames

    def _report_codec_changes(
        self,
        transmission: TransmissionCompleted,
        frames: Sequence[EncodedVoiceFrame],
    ) -> set:
        modes = {frame.compress_mode for frame in frames}
        previous = frames[0].compress_mode
        for position, frame in enumerate(frames[1:], start=2):
            self._raise_if_stopped()
            if frame.compress_mode == previous:
                continue
            before = _codec_name(previous)
            after = _codec_name(frame.compress_mode)
            self.logger.warning(
                "同一 PTT 编码发生变化 uid=%s callsign=%s frame=%d %s->%s",
                transmission.uid,
                transmission.callsign,
                position,
                before,
                after,
            )
            self.event_log.log(
                'recording_codec_changed',
                uid=transmission.uid,
                callsign=transmission.callsign,
                from_codec=before,
                to_codec=after,
                frame=position,
            )
            previous = frame.compress_mode
        return modes

    def _save_wav(
        self,
        transmission: TransmissionCompleted,
        frames: Sequence[EncodedVoiceFrame],
        modes: set,
    ) -> None:
        pcm_parts: List[bytes] = []
        successful_frames = 0
        duration_ms = 0
        previous_mode = None
        decoder = None

        for position, frame in enumerate(frames, start=1):
            self._raise_if_stopped()
            mode = frame.compress_mode
            if mode not in (COMPRESS_RADPCM, COMPRESS_OPUS):
                self.logger.warning(
                    "录音跳过不支持的编码帧 uid=%s callsign=%s frame=%d codec=%s",
                    transmission.uid,
                    transmission.callsign,
                    position,
                    _codec_name(mode),
                )
                continue
            if mode != previous_mode:
                decoder = RadpcmDecoder() if mode == COMPRESS_RADPCM else OpusDecoder()
                previous_mode = mode
            try:
                pcm = decoder.decode(frame.raw)
            except Exception as exc:
                self.logger.warning(
                    "录音帧解码失败 uid=%s callsign=%s frame=%d codec=%s: %s",
                    transmission.uid,
                    transmission.callsign,
                    position,
                    _codec_name(mode),
                    exc,
                )
                continue
            self._raise_if_stopped()
            pcm_parts.append(pcm)
            successful_frames += 1
            duration_ms += frame.duration_ms

        if not pcm_parts:
            self._discard(transmission, 'decode_error')
            return

        codec = _recording_codec(modes)
        target = self._target_path(transmission, '.wav')

        def write_wav(path: str) -> None:
            with wave.open(path, 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                for pcm in pcm_parts:
                    self._raise_if_stopped()
                    output.writeframesraw(pcm)
                    self._raise_if_stopped()

        if self._write_atomic(target, write_wav, transmission):
            self._record_saved(
                transmission,
                target,
                codec,
                successful_frames,
                duration_ms,
                degraded=False,
            )

    def _save_raw(
        self,
        transmission: TransmissionCompleted,
        frames: Sequence[EncodedVoiceFrame],
        suffix: str,
        codec: str,
    ) -> None:
        target = self._target_path(transmission, suffix)
        def write_raw(path: str) -> None:
            with open(path, 'wb') as output:
                for frame in frames:
                    self._raise_if_stopped()
                    output.write(frame.to_bytes())
                    self._raise_if_stopped()

        if self._write_atomic(target, write_raw, transmission):
            self._record_saved(
                transmission,
                target,
                codec,
                len(frames),
                sum(frame.duration_ms for frame in frames),
                degraded=True,
            )

    def _target_path(
        self, transmission: TransmissionCompleted, suffix: str
    ) -> Path:
        assert self.directory is not None
        local = datetime.fromtimestamp(transmission.first_received_wall_time)
        timestamp = (
            local.strftime('%Y%m%d-%H%M%S')
            + f'-{local.microsecond // 1000:03d}'
        )
        callsign = _safe_callsign(transmission.callsign)
        return self.directory / f'{timestamp}-{callsign}-{transmission.uid}{suffix}'

    def _write_atomic(self, target: Path, writer, transmission) -> bool:
        assert self.directory is not None
        temporary = None
        try:
            self._raise_if_stopped()
            handle = tempfile.NamedTemporaryFile(
                prefix='.recording-', suffix='.tmp', dir=self.directory, delete=False
            )
            temporary = handle.name
            handle.close()
            writer(temporary)
            self._raise_if_stopped()
            os.replace(temporary, target)
            return True
        except _RecordingCancelled:
            self._remove_temporary(temporary)
            raise
        except Exception as exc:
            self._remove_temporary(temporary)
            self.logger.warning(
                "录音写盘失败 uid=%s callsign=%s file=%s: %s",
                transmission.uid,
                transmission.callsign,
                target,
                exc,
            )
            self._discard(transmission, 'io_error')
            return False

    def _remove_temporary(self, temporary: Optional[str]) -> None:
        if temporary is None:
            return
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError:
            self.logger.warning("无法清理录音临时文件: %s", temporary)

    def _raise_if_stopped(self) -> None:
        if self._stop.is_set():
            raise _RecordingCancelled()

    def _record_saved(
        self,
        transmission: TransmissionCompleted,
        target: Path,
        codec: str,
        frames: int,
        duration_ms: int,
        degraded: bool,
    ) -> None:
        fields = {
            'uid': transmission.uid,
            'callsign': transmission.callsign,
            'codec': codec,
            'file': str(target),
            'frames': frames,
            'duration_ms': duration_ms,
            'bytes': target.stat().st_size,
        }
        if degraded:
            fields['degraded'] = True
        self.event_log.log('recording_saved', **fields)
        self.cleanup()

    def _discard(self, transmission: TransmissionCompleted, reason: str) -> None:
        self.event_log.log(
            'recording_discarded',
            uid=transmission.uid,
            callsign=transmission.callsign,
            reason=reason,
        )

    def cleanup(self, now: Optional[float] = None) -> None:
        """按年龄及目录总容量删除最旧录音。"""
        if not self.enabled or self.directory is None or self._stop.is_set():
            return
        now = time.time() if now is None else now
        files = self._recording_files()

        if self.max_age_seconds:
            for mtime, name, path, size in files:
                if self._stop.is_set():
                    return
                if now - mtime <= self.max_age_seconds:
                    continue
                self._delete_recording(path, size, 'age_limit')
            files = self._recording_files()

        if not self.max_total_bytes or self._stop.is_set():
            return
        total = sum(item[3] for item in files)
        for _, _, path, size in files:
            if self._stop.is_set():
                return
            if total <= self.max_total_bytes:
                break
            if self._delete_recording(path, size, 'size_limit'):
                total -= size

    def _recording_files(self):
        assert self.directory is not None
        files = []
        try:
            with os.scandir(self.directory) as scan:
                entries = list(scan)
        except OSError as exc:
            self.logger.warning("无法扫描录音目录 %s: %s", self.directory, exc)
            self.event_log.log(
                'recording_cleanup_failed', file=str(self.directory), reason=str(exc)
            )
            return files
        for entry in entries:
            if self._stop.is_set():
                break
            if Path(entry.name).suffix.lower() not in _AUDIO_SUFFIXES:
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                self.logger.warning("无法读取录音文件信息 %s: %s", entry.path, exc)
                self.event_log.log(
                    'recording_cleanup_failed', file=entry.path, reason=str(exc)
                )
                continue
            files.append((stat.st_mtime, entry.name, Path(entry.path), stat.st_size))
        files.sort(key=lambda item: (item[0], item[1]))
        return files

    def _delete_recording(self, path: Path, size: int, reason: str) -> bool:
        try:
            path.unlink()
        except OSError as exc:
            self.logger.warning("无法清理录音文件 %s: %s", path, exc)
            self.event_log.log(
                'recording_cleanup_failed', file=str(path), reason=str(exc)
            )
            return False
        self.event_log.log(
            'recording_deleted', file=str(path), bytes=size, reason=reason
        )
        return True


def _safe_callsign(callsign: str) -> str:
    safe = re.sub(r'[^\w.-]', '_', callsign or '')
    return 'UNKNOWN' if safe in ('', '.', '..') else safe


def _codec_name(mode: int) -> str:
    return _CODEC_NAMES.get(mode, f'UNKNOWN({mode})')


def _recording_codec(modes: set) -> str:
    if len(modes) != 1:
        return 'MIXED'
    return _codec_name(next(iter(modes)))
