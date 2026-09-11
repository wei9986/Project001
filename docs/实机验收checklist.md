# 实机验收 Checklist（比赛前 48 小时）

打印本页，逐项勾选。未完成项不得进入正式比赛运行。

## 门禁命令

在项目根目录 `Project001-opencv.gcs/` 执行：

```text
python -m tools.启动检查 --profile simulation --json
python -m tools.启动检查 --profile live --json
python -m tools.启动检查 --profile competition --json
python -m unittest discover -s tests -v
python -m tools.仿真验证 --output logs/pre_match_simulation.json
python -m tools.离线评测 --output logs/pre_match_offline.json
python -m tools.标定验收 --images "data/calibration/*.jpg" --output config/camera_calibration.json --report logs/calib_gate.json
```

- `simulation`：开发合并门禁（坐标全 0 仅 warning）
- `live`：实机联调门禁（坐标 / 标定缺失 → fail）
- `competition`：比赛门禁（另要求精定位开启、传感器已接入）

## 软件回归

- [ ] `unittest discover` 全绿
- [ ] `tools.仿真验证` passed
- [ ] `tools.离线评测` 全部场景 passed
- [ ] `tools.启动检查 --profile live` passed
- [ ] `tools.启动检查 --profile competition` passed（比赛当日）

## 标定与坐标

- [ ] 标定图 ≥ 10 张（同分辨率、多角度）
- [ ] `标定验收` RMS < 0.5 px
- [ ] 平面映射误差 < 5 mm（若使用 world_mm / 精定位）
- [ ] `config/实机配置.json` 的 `calibration` 指向有效文件
- [ ] `config/任务配置.json` 四工位坐标非全 0，人工抽检到位误差可接受

## 传感器与安全

- [ ] `sensor_provider` 或等价数据源已接入（ToF / 限位 / 抓取确认）
- [ ] ToF 遮挡：动作被拦，`safety_fault` 入日志
- [ ] 抓取未确认：装配被拦
- [ ] 抓取确认后：装配放行
- [ ] 限位触发：动作被拦 / safe_stop

## 精定位与任务

- [ ] `use_fine_localization=true`（competition）
- [ ] 抓取前出现 `fine_localization_started` / `stable` 日志
- [ ] 完整任务（两批物料）实机跑通 ≥ 3 次
- [ ] 任务二维码串口扫码成功 ≥ 3 次（`max_line_length=512`）

## 异常演练

- [ ] 拔相机线：仍 tick，可 recover 或超时停止（不永久卡死）
- [ ] 拔控制器串口：记录错误并进入恢复/停止
- [ ] 遮挡 AprilTag：超时恢复或安全停止
- [ ] 心跳静默：出现 `heartbeat_offline`

## F407 联调

- [ ] PING → ACK/DONE
- [ ] PICK / PLACE / ASSEMBLE / HOME 均有 ACK 后 DONE
- [ ] FAIL / 超时路径进入 recover
- [ ] safe_stop 可下发

## 日志与证据

- [ ] 会话目录含 `events.jsonl` / `actions.jsonl` / `summary.json`
- [ ] action 日志与真实下发一致（安全拦截的动作未出现 PICK）
- [ ] 本清单签字：________  日期：________
