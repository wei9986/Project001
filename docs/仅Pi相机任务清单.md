# 仅 Pi 4B + 相机 · 任务清单

> 适用条件：手头只有树莓派 4B 且相机可出图；无 F407 / 底盘 / GM865 / 夹爪。  
> 目标：把「看得见、参数稳、标定准、日志可复盘」做到可交接；整机闭环等硬件到位后再做。  
> 工作目录：`视觉模块开发/Project001-opencv.gcs`  
> 建议解释器：项目上级 `.venv` 或树莓派上项目内 `.venv`

---

## 0. 环境（第 0 天）

- [ ] SSH / 本机进入树莓派，确认相机预览有画面
- [ ] 进入工程目录，激活 venv
- [ ] `pip install -r requirements.txt`
- [ ] `python -m tools.启动检查 --json` → `passed: true`
- [ ] `python -m unittest discover -s tests -v` → 全部通过

**命令示例（树莓派）：**

```bash
cd ~/路径/Project001-opencv.gcs
source .venv/bin/activate   # 或上级目录 .venv
python -m tools.启动检查 --json
python -m unittest discover -s tests -v
```

---

## 1. 相机稳态（第 1 天）

- [ ] 确认 `config/实机配置.json` 里 `camera.source`（多为 `0`）、`width/height/fps` 与实机一致
- [ ] 跑颜色追踪并叠画面：

```bash
python -m vision.颜色追踪 \
  --source 0 \
  --config config/颜色追踪配置.json \
  --colors red green blue \
  --log logs/vision_pi_smoke.jsonl
```

- [ ] 连续运行 **≥30 分钟**不崩溃、不明显掉帧
- [ ] 记录：实际分辨率、帧率、相机安装高度、镜头型号、室内光照条件（写进 `docs/measurement.md` 或笔记）

**本阶段不要：** `--mode live`（会找不存在的串口）

---

## 2. 颜色调参（第 1–2 天）

准备：红 / 绿 / 蓝色块（色纸、积木、物料均可）

- [ ] 备份配置：`cp config/颜色追踪配置.json config/颜色追踪配置_备份_YYYYMMDD.json`
- [ ] 逐色调参（OpenCV 滑条或 Qt）：

```bash
# 滑条版
python -m tools.参数调试 --source 0 --config config/颜色追踪配置.json --color red
# 再改 --color green / blue；调完按 s 保存、q 退出

# 或 Qt 版
python -m tools.qt调参
```

- [ ] 固定距离与高度下，三色均可稳定检出（连续多帧 `valid`）
- [ ] 换一种光照再验一次（开灯/关灯或换窗口），必要时微调并再备份
- [ ] 归档 10～20 张失败/边界样例到会话或 `logs/error_samples_manual/`

---

## 3. 相机标定（第 2–3 天）

- [ ] 打印棋盘格（建议内角点 9×6，方格边长已知，如 25mm）
- [ ] 采集 ≥10 张不同角度/位置图到 `data/calibration/`
- [ ] 跑标定：

```bash
python -m vision.相机标定 \
  --images 'data/calibration/*.jpg' \
  --board-width 9 \
  --board-height 6 \
  --square-size 25 \
  --output config/camera_calibration.json
```

- [ ] （可选）准备 `data/calibration/plane_points.json`，追加平面映射，使结果含 `world_mm`
- [ ] 在 `config/实机配置.json` 填写 `"calibration": "config/camera_calibration.json"`
- [ ] **保持** `"use_fine_localization": false`（无下位机先不要开抓取精定位）
- [ ] 带标定再跑一次颜色追踪，确认不报错、日志含坐标状态

---

## 4. 相机扫码（第 3–4 天，无 GM865）

- [ ] 将 `config/实机配置.json` 中 `scanner.mode` 临时改为 `camera_qr`（仅本机试验；有 GM865 后再改回 `serial`）
- [ ] 把 `config/仿真任务.json` 内容做成二维码并打印
- [ ] 用 OpenCV/扫码路径验证能读出任务码（可用小脚本或后续 live 联调前的扫码单测）
- [ ] 非法码 / 残缺码：确认不会直接崩进程（仿真里已有重试逻辑）

**扫码自检可先走仿真：**

```bash
python -m app.视觉应用 --mode simulation --task config/仿真任务.json
```

---

## 5. AprilTag 工位（第 4 天）

- [ ] 打印 AprilTag 36h11：id 1 / 2 / 3（对应 `config/站点标签.json`）
- [ ] Qt 调参「站点」页或代码检测：能映射到 `material_area` / `assembly_area` / `finished_area`
- [ ] 记录 tag 实际边长 mm，必要时改 `tag_size_mm`
- [ ] 拍摄一张「三站同框」标注图存档

---

## 6. 每日回归（贯穿全程）

每天至少一次：

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m tools.离线评测`
- [ ] `python -m app.视觉应用 --mode simulation`

可选：

```bash
python -m tools.仿真验证 --frames 30 --timeout-ms 50
```

---

## 7. 文档与备份（第 5 天收口）

- [ ] 更新/新建 `docs/measurement.md`：相机高度、视野、标定误差、曝光参数
- [ ] 列出本周配置文件版本与备份路径
- [ ] 网盘备份：`config/*.json`（含标定）、样例图、`logs/` 关键 jsonl（体积大的数据集另存）
- [ ] 录 1～2 分钟演示视频：出图 + 三色识别 +（可选）Tag/扫码
- [ ] Git：提交代码与文档（勿提交 `.venv`、大数据集、权重，遵循仓库 `.gitignore`）

---

## 8. 两周出口验收（勾完即算阶段完成）

- [ ] Pi 可稳定出图 ≥30 min
- [ ] 三色识别有现场调参配置 + 备份
- [ ] `camera_calibration.json` 存在且路径已写入实机配置
- [ ] 仿真 S0–S5 与单元测试全绿
- [ ] 有测量笔记 + 演示视频/截图
- [ ] 明确记录：**等 F407 到位后的第一项联调 = PING↔ACK/DONE**

---

## 明确暂缓（无硬件不做）

| 项 | 原因 |
|---|---|
| `app.视觉应用 --mode live` 接控制器 | 无串口设备会报错/恢复空转 |
| 打开 `use_fine_localization` | 缺动作反馈与抓取台架 |
| STM32 固件 / 底盘 / 夹爪 | 未到货 |
| YOLO 进主路径 | 非阻塞；有余力可在 PC 标数据 |

---

## 硬件一到货就接上的顺序（备忘）

1. F407 确认表（去重 / 心跳 / RECOVER）  
2. 921600 串口 PING  
3. `scanner` 定案（serial vs camera_qr）  
4. `--mode live` 冒烟  
5. 再开精定位开关  

---

## 进度记录（自行填写）

| 日期 | 完成项 | 阻塞 | 备注 |
|---|---|---|---|
|  |  |  |  |
|  |  |  |  |
