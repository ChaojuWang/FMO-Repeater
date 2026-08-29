# 服务层设计

> Merged from changes/001

## 1. 配置（config.py）

```yaml
mqtt: {broker, port, username, password, client_id_prefix, keepalive}
topics: {subscribe, publish}
echo:
  timeout: 5.0          # 流结束判定超时（秒）
  vendor: 0x2000        # 重放 vendor（校验拒绝保留区）
  uid: 65535            # 重放 UID（默认 65535；0=保持原值，但会被客户端
                        # 按"自己发的"自过滤——设备收不到回声，勿用）
  callsign_prefix: 'RE>'
event_log: {enabled, file, max_bytes, backup_count}
logging: {level, console, file, max_bytes, backup_count}
daemon: {enabled, pid_file}
```

`load_config`：默认配置 + YAML deep_merge；`validate_config`：必需节、
vendor 保留区拒绝、echo/event_log 参数校验。

## 2. Echo 服务（echo.py）

### 2.1 防循环（决策 D1，替代旧 UID=65535 机制）

跳过条件（任一）：
- `header.vendor == echo.vendor` 且 `callsign.startswith(prefix)`
- `echo.uid != 0` 且 `header.uid == echo.uid`

默认（uid=65535）双条件同时生效：软件区同 vendor 的他人包或恰好带前缀的
他人包单独出现都不误判。**uid=65535 的来源**（D8）：客户端按 UID 做
自回声抑制，重放包保持原 UID 会被设备当作"自己发的"丢弃（实测回归）；
固定重放 UID 65535 与旧版 Echo 行为一致，同时使 UID 匹配参与防循环。

### 2.2 头重写

`vendor → echo.vendor`、`callsign → prefix+原呼号`、`timestamp → now(ms)`；
`uid`（echo.uid≠0 时）、`stream_begin_utc` 保持原值；**帧区与 CRC 不动**
（CRC 仅覆盖帧区，帧区不变则校验值仍有效）。

### 2.3 流结束与重放

- 缓存 `(ParsedPacket, monotonic)`，`last_message_time` 为 wall clock
- 超时（echo.timeout 无新消息）→ 整批取出重放（锁外执行，不阻塞 MQTT 线程）
- 重放按原始接收时间轴：绝对截止时间 `started_at + (t[i]-t[0])`，防累积漂移

### 2.4 解析容错

ProtocolError → `invalid_packets++` + 事件 `packet_invalid`，服务不中断。

## 3. 事件日志（event_log.py）

JSONL，每行 `{"ts": ISO8601, "event": ..., ...}`；RotatingFileHandler 按字节轮转；
enabled=false 全 no-op。事件模式：

| event | 字段 |
|---|---|
| service_started | version, vendor, subscribe_topic, echo_timeout |
| stream_start | uid, callsign, vendor, stream_begin_utc, frames |
| packet_received | uid, callsign, vendor, frames, bytes, checksum_ok |
| packet_invalid | reason, bytes |
| loop_detected | uid, callsign, vendor |
| replay_started | uid, callsign, packets |
| replay_finished | uid, callsign, packets, ok, failed, duration_s |
| stream_end | uid, callsign, packets, duration_s |
| service_stopped | reason |

## 4. 运行日志（logging_setup.py）

文本格式（时间-名称-级别-消息），控制台 + RotatingFileHandler，配置于 logging 节。

## 5. 守护进程（daemon.py）

自旧实现原样迁移：双 fork、PID 文件、SIGTERM/SIGINT、start/stop/restart/status。
仅 Unix/Linux。
