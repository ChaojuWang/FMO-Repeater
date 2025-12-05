# FMO Echo 服务测试套件

本目录包含 FMO Echo 服务的开发者测试，用于验证核心功能的正确性。

## 测试文件说明

### 1. test_header.py - 头部处理测试
测试 FMO 数据包头部的解析、序列化和修改功能。

**测试内容：**
- ✅ 头部解析（from_bytes）
- ✅ 头部序列化（to_bytes）
- ✅ 往返转换（序列化后再反序列化）
- ✅ 头部字段修改
- ✅ 完整数据流修改（replace_header_in_stream）
- ✅ 边界条件测试

**运行方式：**
```bash
python tests/test_header.py
```

### 2. test_config.py - 配置管理测试
测试配置文件的加载、合并和验证功能。

**测试内容：**
- ✅ 默认配置加载
- ✅ YAML 文件加载
- ✅ 配置合并（用户配置覆盖默认配置）
- ✅ 配置验证（必需字段、数据类型、取值范围）
- ✅ 错误配置检测

**运行方式：**
```bash
python tests/test_config.py
```

### 3. test_uid_filter.py - UID 过滤测试
测试 UID 过滤机制，确保不会重放循环。

**测试内容：**
- ✅ 原始消息识别（UID ≠ 65535）
- ✅ 重放消息识别（UID = 65535）
- ✅ 过滤逻辑正确性
- ✅ 避免无限循环

**运行方式：**
```bash
python tests/test_uid_filter.py
```

### 4. test_message_flow.py - 消息流程测试
测试完整的消息处理流程（缓存、超时检测、重放）。

**测试内容：**
- ✅ 消息缓存机制
- ✅ 超时检测逻辑
- ✅ 重放触发时机
- ✅ 头部修改正确性
- ✅ 缓存清理

**运行方式：**
```bash
python tests/test_message_flow.py
```

### 5. test_integration.py - 集成测试
端到端集成测试（需要 MQTT 服务器）。

**测试内容：**
- ✅ MQTT 连接和订阅
- ✅ 消息接收和处理
- ✅ 完整的 echo 流程
- ⚠️ 需要配置有效的 MQTT 服务器

**运行方式：**
```bash
# 需要先配置 config.yaml 中的 MQTT 连接信息
python tests/test_integration.py
```

## 运行所有测试

使用提供的测试运行脚本：

```bash
python tests/run_all_tests.py
```

或者手动运行每个测试：

```bash
python tests/test_header.py
python tests/test_config.py
python tests/test_uid_filter.py
python tests/test_message_flow.py
# python tests/test_integration.py  # 需要 MQTT 服务器
```

## 测试数据

所有测试使用 `demo.py` 中相同的示例数据包，确保测试的一致性。

**示例消息特征：**
- 原始 UID: 441
- 原始呼号: "BD8BOJ"
- 数据包大小: 约 600 字节
- 包含完整的 22 字节头部和载荷

## 注意事项

1. **单元测试**（test_header.py, test_config.py, test_uid_filter.py）
   - 不需要外部依赖
   - 可以随时运行
   - 快速验证核心逻辑

2. **功能测试**（test_message_flow.py）
   - 模拟服务内部逻辑
   - 不需要实际的 MQTT 连接
   - 测试超时和缓存机制

3. **集成测试**（test_integration.py）
   - 需要实际的 MQTT 服务器
   - 测试完整的端到端流程
   - 运行前确保 config.yaml 配置正确

## 测试覆盖率

当前测试覆盖的核心功能：

- [x] 头部解析和序列化
- [x] 配置管理
- [x] UID 过滤机制
- [x] 消息缓存
- [x] 超时检测
- [x] 消息重放
- [x] 头部修改
- [ ] 守护进程功能（需要手动测试）
- [ ] 日志轮转（需要长时间运行）
- [ ] 信号处理（需要手动测试）

## 添加新测试

创建新测试文件时，请遵循以下规范：

1. 文件名以 `test_` 开头
2. 包含清晰的测试说明和预期结果
3. 使用 `print()` 输出测试过程和结果
4. 最后打印 `✅ 所有测试通过` 或 `❌ 测试失败`
5. 更新本 README.md 文档

## 问题报告

如果测试失败，请检查：
1. 依赖是否正确安装（`pip install -r requirements.txt`）
2. Python 版本是否 >= 3.6
3. 对于集成测试，MQTT 服务器是否可访问
4. 配置文件格式是否正确

如遇到问题，请查看测试输出的详细错误信息。
