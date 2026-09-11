# 视觉模块实机操作 SOP

## 启动前

1. 确认 Pi、相机和 F407 共地，电机动力与 Pi 电源隔离。
2. 默认扫码模式以 `config/实机配置.json` 为准：当前为 `scanner.mode=serial`（GM865，`/dev/ttyUSB0`，9600），`max_line_length=512` 可装下长任务码；若改用相机读码，设为 `camera_qr`。
3. F407 控制器串口默认 **921600**（`controller.port=/dev/ttyUSB1`），与方案 B 对齐；协议含 `ack_timeout_ms` / `done_timeout_ms`（ACK 与 DONE 分阶段超时）。
4. 放置红、绿、蓝物料到固定相机视野，检查 `颜色追踪配置.json` 中 ROI 和阈值。
5. 首次运行设置 `display: true` 调整画面；验收和长稳运行设置回 `false`。
6. `use_fine_localization` 默认 `false`；仅在完成平面标定（`calibration` 指向有效 JSON）后开启；比赛门禁 `--profile competition` 要求开启。
7. 启动检查：`python -m tools.启动检查 --profile live`；完整清单见 [实机验收checklist.md](实机验收checklist.md)。
8. 标定验收：`python -m tools.标定验收 --images "data/calibration/*.jpg"`。

## 验收顺序

1. 启动后观察 `events.jsonl` 是否出现控制器 `ACK/DONE`，先验证 `PING`。
2. 连续采集 30 分钟，检查 `summary.json` 中掉帧、平均 FPS、欠压与相机重连次数。
3. 用真实任务二维码测试 50 次；每次的距离、角度、光照另行记录到 `scans.csv` 扩展列或测试表。
4. 红绿蓝各采集至少 30 张实际物料图；错误样本保存在会话 `error_samples/`，用于修改配置后回放。
5. 扫真实任务码，确认 F407 每个动作均产生 `ACK` 后 `DONE`。拔掉控制器串口时系统应记录错误并进入恢复/停止，不能继续下发动作。
6. 断心跳 / 相机掉线时应仍能触发状态超时恢复（不得永久卡死）。

## 颜色调参

使用 `python -m tools.参数调试 --source 0 --config config/颜色追踪配置.json --color red`，或 `python -m tools.qt调参`。修改阈值后保留一次会话目录和对应配置版本；不要直接覆盖已通过验收的配置。

## 部署说明

- 树莓派脚本默认解释器：`${ROOT}/.venv/bin/python`；Windows 开发机请改用 `..\ .venv\Scripts\python.exe` 或设置 `PYTHON_BIN`。
- systemd 单元 `deploy/视觉模块仿真服务.service` 中 `User=pi`、`WorkingDirectory=/opt/vision-module` 需按现场修改；仿真单次跑完 exit 0 后不会自动重启（`Restart=on-failure`）。
