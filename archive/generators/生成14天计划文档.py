from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "14天树莓派视觉模块开发计划.docx"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_geometry(table, widths):
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(widths)))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), "120")
    tbl_ind.set(qn("w:type"), "dxa")
    grid = tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(width))
            tc_w.set(qn("w:type"), "dxa")
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def set_run_font(run, name="Calibri", size=11, color=None, bold=None, italic=None):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def style_document(doc):
    section = doc.sections[0]
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.25

    for name, size, color, before, after in (
        ("Heading 1", 16, "2E74B5", 18, 10),
        ("Heading 2", 13, "2E74B5", 14, 7),
        ("Heading 3", 12, "1F4D78", 10, 5),
    ):
        style = styles[name]
        style.font.name = "Calibri"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        style.font.bold = True
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    code_style = styles.add_style("Code Block", WD_STYLE_TYPE.PARAGRAPH)
    code_style.font.name = "Consolas"
    code_style._element.rPr.rFonts.set(qn("w:ascii"), "Consolas")
    code_style._element.rPr.rFonts.set(qn("w:hAnsi"), "Consolas")
    code_style.font.size = Pt(9)
    code_style.paragraph_format.left_indent = Inches(0.2)
    code_style.paragraph_format.space_after = Pt(6)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header.add_run("Project001-opencv.gcs | 14 天树莓派开发计划")
    set_run_font(run, size=9, color="6B7280")
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("视觉模块开发计划")
    set_run_font(run, size=9, color="6B7280")


def add_title(doc):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(36)
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run("14 天树莓派视觉模块开发计划")
    set_run_font(run, size=24, color="0B2545", bold=True)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.paragraph_format.space_after = Pt(22)
    run = p2.add_run("工创智能物流机器人 | 每天最多 2 小时 | 进入 9 月控制联调")
    set_run_font(run, size=12, color="5B6470")
    callout = doc.add_table(rows=1, cols=1)
    set_table_geometry(callout, [9360])
    cell = callout.cell(0, 0)
    set_cell_shading(cell, "F4F6F9")
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run("最终目标：")
    set_run_font(run, size=11, color="1F4D78", bold=True)
    run = p.add_run("完成可迁移到树莓派的基本视觉软件闭环。真实场地精度、真实串口和机械抓取效果将在实机及 9 月联调阶段继续验证。")
    set_run_font(run, size=11)
    doc.add_paragraph()


def add_heading(doc, text, level=1):
    doc.add_heading(text, level=level)


def add_bullets(doc, items):
    for item in items:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(3)
        p.add_run(item)


def add_steps(doc, items):
    for item in items:
        p = doc.add_paragraph(style="List Number")
        p.paragraph_format.space_after = Pt(3)
        p.add_run(item)


def add_code(doc, text):
    for line in text.strip("\n").splitlines():
        p = doc.add_paragraph(style="Code Block")
        p.add_run(line)


def add_labeled(doc, label, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    run = p.add_run(f"{label}：")
    set_run_font(run, size=11, color="1F4D78", bold=True)
    p.add_run(text)


def add_day(doc, day, title, files, task, steps, goal, verify, deliverables, command):
    add_heading(doc, f"第 {day} 天｜{title}", level=1)
    add_labeled(doc, "主任务", task)
    add_labeled(doc, "重点文件", files)
    add_heading(doc, "具体步骤", level=2)
    add_steps(doc, steps)
    add_heading(doc, "当天实现目标", level=2)
    doc.add_paragraph(goal)
    add_heading(doc, "验证方法", level=2)
    add_bullets(doc, verify)
    add_code(doc, command)
    add_heading(doc, "当天交付物", level=2)
    add_bullets(doc, deliverables)
    add_labeled(doc, "GitHub 记录", f"填写 docs/progress/day{day:02d}.md，提交信息使用 day{day:02d}: {title.lower()}")


def build():
    doc = Document()
    style_document(doc)
    add_title(doc)

    add_heading(doc, "一、14 天总览", level=1)
    doc.add_paragraph("每天最多投入 2 小时。每天只有一个主开发任务，阅读、编码、测试、日志和 GitHub 提交都围绕当天任务展开。当天验收未通过时，优先修复，不跳到下一天。")
    table = doc.add_table(rows=1, cols=4)
    set_table_geometry(table, [850, 2350, 3500, 2660])
    headers = ["天数", "主任务", "怎么做", "目标"]
    for cell, text in zip(table.rows[0].cells, headers):
        set_cell_shading(cell, "E8EEF5")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        set_run_font(r, size=10, color="0B2545", bold=True)
    overview = [
        ("1", "VSCode 环境", "选择解释器、检查、测试、断点", "项目可运行"),
        ("2", "运行时循环", "调试 step、状态、动作和日志", "模拟任务完成"),
        ("3", "虚拟设备", "验证相机、扫码器、传感器接口", "设备可替换"),
        ("4", "颜色检测", "阅读算法并理解检测结果", "知道结果如何产生"),
        ("5", "颜色调参", "一次修改一个参数并比较报告", "得到可复现参数"),
        ("6", "跟踪精定位", "验证确认、丢失、跳变和稳定", "不稳定目标不抓取"),
        ("7", "标定转换", "验证标定文件和平面坐标", "输出 world_mm"),
        ("8", "AprilTag", "验证工位映射、丢失和未知 ID", "工位识别可靠"),
        ("9", "扫码任务", "验证正常和错误任务", "非法任务被拒绝"),
        ("10", "STM32 协议", "验证 ACK、DONE、FAIL 和重试", "通信闭环可对接"),
        ("11", "安全传感器", "注入 ToF、限位和抓取故障", "危险动作被阻止"),
        ("12", "故障回放", "运行正常和异常场景", "生成故障报告"),
        ("13", "树莓派部署", "安装环境并运行模拟", "树莓派可运行"),
        ("14", "实机回归", "服务、设备单测和最终检查", "形成联调版本"),
    ]
    for row in overview:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.add_run(text)

    doc.add_page_break()
    add_heading(doc, "二、每日执行规范", level=1)
    add_labeled(doc, "时间分配", "10 分钟阅读目标，70 分钟开发或调试，30 分钟测试和日志，10 分钟记录和 GitHub 提交。")
    add_labeled(doc, "每日固定检查", "运行单元测试、检查日志、填写当天进度文件，并提交到 GitHub。")
    add_code(doc, "& '..\\.venv\\Scripts\\python.exe' -m unittest discover -s tests -q\n& '..\\.venv\\Scripts\\python.exe' -m tools.启动检查 --json")
    add_labeled(doc, "开发原则", "一次只解决一个问题；一次只修改一个关键参数；先写测试再改实现；不删除失败测试。")

    days = [
        (1, "配置 VSCode 开发环境", "`.vscode/`、`tools/启动检查.py`、`tests/`", "让 VSCode 能正确运行项目、测试和调试。", ["打开项目根目录，不要只打开单个 Python 文件。", "选择 `..\\.venv\\Scripts\\python.exe` 作为解释器。", "运行启动前检查和全部单元测试。", "按 F5 选择 Application Simulation，并设置一个断点。", "查看 `logs/` 是否生成模拟运行记录。"], "能够在 VSCode 中运行、暂停、继续和测试项目。", ["测试结果为 OK。", "模拟任务最终状态为 complete。", "解释器路径不是系统 Python。"], ["VSCode 配置有效。", "基础测试结果。", "docs/progress/第1天.md。"], "& '..\\.venv\\Scripts\\python.exe' -m tools.启动检查 --json\n& '..\\.venv\\Scripts\\python.exe' -m unittest discover -s tests -v\n& '..\\.venv\\Scripts\\python.exe' -m app.视觉应用 --mode simulation"),
        (2, "跑通统一运行时循环", "`app/运行循环.py`、`app/视觉应用.py`、`app/运行观测.py`", "理解运行时如何连接设备、视觉、状态机和日志。", ["在 `RuntimeLoop.start()` 设置断点。", "在 `RuntimeLoop.step()` 设置断点。", "观察当前状态、事件和动作。", "观察视觉结果和工位结果。", "查看动作日志和状态日志。"], "能解释一次任务如何从启动走到 complete。", ["状态经过 S0 到 COMPLETE。", "每个动作都有日志。", "目标和工位结果被运行时读取。"], ["运行时调试记录。", "模拟任务日志。", "docs/progress/day02.md。"], "& '..\\.venv\\Scripts\\python.exe' -m app.视觉应用 --mode simulation --log logs\\day02_application.jsonl"),
        (3, "完成虚拟设备接入", "`app/虚拟设备.py`、`app/运行循环.py`", "确认摄像头、扫码器和传感器可以被替换。", ["阅读 VirtualCamera.read()。", "测试读帧成功和读完帧。", "阅读 VirtualScanner.read()。", "测试有扫码和无扫码。", "修改 VirtualSensors 的 ToF、限位和抓取值。", "确认状态机不直接依赖真实设备。"], "虚拟设备接口可用于完整任务测试。", ["虚拟设备测试通过。", "空数据不会导致无日志崩溃。", "设备替换不要求重写状态机。"], ["虚拟设备测试结果。", "设备接口说明。", "docs/progress/day03.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_stage2_workflow.Stage2Tests.test_virtual_devices_and_memory_transport_are_deterministic -v"),
        (4, "完成颜色检测基础学习", "`vision/颜色追踪.py`、`config/颜色追踪配置.json`", "理解 HSV、轮廓、面积、圆度和目标结果。", ["阅读 load_config()。", "阅读 ColorTracker.track()。", "查看颜色掩膜和轮廓过滤。", "阅读 Target.to_dict()。", "运行颜色检测测试。", "记录一个目标被保留或过滤的原因。"], "能解释颜色检测结果中 targets、valid、confidence 和坐标字段。", ["小噪声被过滤。", "未确认目标不能用于抓取。", "测试全部通过。"], ["颜色检测学习记录。", "测试输出。", "docs/progress/day04.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_color_tracking -v"),
        (5, "完成颜色检测参数调试", "`config/颜色追踪配置.json`、`tools/仿真验证.py`", "通过单变量实验得到可复现的检测参数。", ["备份颜色配置。", "只修改 min_area 并运行报告。", "恢复后只修改 confirm_frames。", "恢复后只修改 max_lost_frames。", "比较三份 JSON 报告。", "选择没有明显退化的一组参数。"], "知道误检、漏检、确认延迟和闪烁分别应调整什么。", ["每次只改一个参数。", "模拟验证通过。", "现有回归测试没有减少。"], ["参数实验报告。", "最终配置。", "docs/progress/day05.md。"], "& '..\\.venv\\Scripts\\python.exe' -m tools.仿真验证 --output logs\\day05_visual.json\n& '..\\.venv\\Scripts\\python.exe' -m unittest discover -s tests -q"),
        (6, "完成目标跟踪和精定位验证", "`vision/精定位.py`、`tests/`", "确保只有稳定目标才允许抓取。", ["阅读稳定帧计数。", "阅读坐标跳变拒绝。", "阅读目标丢失超时。", "运行正常精定位测试。", "运行目标丢失和跳变测试。", "检查稳定前后的结果差异。"], "不稳定、跳变或丢失目标不能触发抓取。", ["未确认不抓取。", "跳变被拒绝。", "丢失后不使用旧坐标。", "稳定后输出 stable。"], ["精定位测试结果。", "稳定性结论。", "docs/progress/day06.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_stage2_workflow.Stage2Tests.test_fine_localization_rejects_jump_and_confirms_stable_mean -v"),
        (7, "完成相机标定和坐标转换验证", "`vision/相机标定.py`、`config/`", "验证像素坐标到场地坐标的转换接口。", ["阅读内参和畸变校正。", "阅读标定文件保存和加载。", "阅读平面单应性转换。", "运行标定测试。", "检查四角和中心点误差。", "检查无标定和损坏文件行为。"], "完成标定后才能输出可信的 world_mm。", ["标定文件可读取。", "分辨率匹配。", "转换误差在允许范围内。", "异常标定文件拒绝启动。"], ["坐标转换测试。", "误差记录。", "docs/progress/day07.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_camera_calibration -v"),
        (8, "完成 AprilTag 工位识别", "`vision/站点定位.py`、`config/站点标签.json`", "让 Tag 识别结果可以安全交给运行时。", ["检查字典和 Tag 尺寸。", "检查 ID 到 station ID 的映射。", "运行正常 Tag 测试。", "测试无 Tag、未知 Tag 和多 Tag。", "查看中心点、角点和位姿字段。", "记录失败场景。"], "输出可靠的工位识别结果。", ["正常 Tag 识别。", "未知或丢失工位不能继续执行。", "多 Tag 不产生错误映射。"], ["工位识别测试。", "站点映射配置。", "docs/progress/day08.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_stage2_workflow.Stage2Tests.test_apriltag_station_detection_works_without_camera_calibration -v"),
        (9, "完成扫码和任务计划验证", "`mission/扫码读取.py`、`mission/任务计划.py`、`config/仿真任务.json`", "确保任务输入正确、物料状态转换合法。", ["阅读任务字段检查。", "复制模拟任务配置。", "修改一个物料并测试。", "构造字段缺失、颜色错误和环码错误。", "构造重复物料。", "检查错误是否拒绝。"], "错误任务不能进入执行流程。", ["正常任务可解析。", "非法任务被拒绝。", "物料状态不能非法跳转。"], ["任务解析测试。", "错误任务记录。", "docs/progress/day09.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_stage2_workflow.Stage2Tests.test_external_scanner_and_task_qr_validation -v"),
        (10, "完成 STM32 协议闭环", "`protocol/串口协议.py`、`protocol/transport.py`、`protocol/simulation.py`", "验证状态机动作通过内存串口得到虚拟 STM32 响应。", ["阅读帧头、版本、序号、命令、长度和 CRC。", "测试正常 ACK 和 DONE。", "测试 FAIL 和错误码。", "测试 DONE 丢失和重试。", "检查半包、CRC 和序号错误。"], "协议动作可直接与控制端约定的接口对接。", ["ACK、DONE、FAIL 均可处理。", "超时可重试。", "错误码被记录。", "重试超过上限后安全失败。"], ["协议测试结果。", "协议字段说明。", "docs/progress/day10.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_stage2_workflow.Stage2Tests.test_protocol_action_bridge_completes_over_memory_serial -v\n& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_stage2_workflow.Stage2Tests.test_protocol_action_bridge_retries_when_done_is_dropped -v"),
        (11, "完成传感器和安全策略", "`app/安全监控.py`、`app/虚拟设备.py`", "在传感器不安全时阻止普通动作。", ["测试 ToF 正常距离。", "测试 ToF 过近。", "测试限位触发。", "测试抓取未确认。", "检查 safety_fault。", "检查恢复次数和 safe_stop。"], "危险条件下不发送普通动作，并进入恢复或安全停止。", ["故障有明确原因。", "普通动作被阻止。", "恢复失败进入 FAILED。", "日志有安全故障记录。"], ["安全策略测试。", "故障原因记录。", "docs/progress/day11.md。"], "& '..\\.venv\\Scripts\\python.exe' -m unittest tests.test_stage2_workflow.Stage2Tests.test_safety_monitor_blocks_sensor_faults -v"),
        (12, "完成离线故障回放", "`tools/离线评测.py`、`app/运行观测.py`", "用统一命令验证正常和异常场景。", ["运行正常任务。", "运行目标丢失。", "运行 AprilTag 丢失。", "运行无效扫码。", "运行串口断开。", "查看 JSON 报告并记录预期和实际结果。"], "所有故障场景都有明确最终状态和原因。", ["正常场景 complete。", "故障场景进入预期状态。", "报告自动生成。", "日志能还原过程。"], ["离线评测报告。", "故障场景结论。", "docs/progress/day12.md。"], "& '..\\.venv\\Scripts\\python.exe' -m tools.离线评测 --output logs\\day12_evaluation.json"),
        (13, "在树莓派上部署并运行模拟系统", "`requirements.txt`、`tools/启动检查.py`、`scripts/`、`deploy/`", "在真实树莓派 CPU、内存和文件系统上运行模拟任务。", ["将代码复制或克隆到树莓派。", "安装 Python、venv 和 pip。", "创建 .venv。", "安装 requirements.txt。", "运行 preflight。", "运行模拟任务。", "观察 CPU、内存和日志。", "检查启动脚本和 systemd 文件。"], "树莓派可以稳定运行项目软件。", ["模拟任务完成。", "依赖检查通过。", "日志持续生成。", "资源没有持续异常增长。"], ["树莓派安装记录。", "模拟运行日志。", "docs/progress/day13.md。"], "sudo apt update\nsudo apt install -y python3 python3-venv python3-pip\npython3 -m venv .venv\nsource .venv/bin/activate\npython -m pip install -r requirements.txt\npython -m tools.启动检查 --json\npython -m app.视觉应用 --mode simulation --log logs/day13_raspberry_pi.jsonl"),
        (14, "完成树莓派实际验证和最终回归", "树莓派系统、systemd、设备接口、`tests/`", "在树莓派上完成最终测试、服务启动和已到位设备的单独验证。", ["运行 preflight。", "运行全部单元测试。", "运行离线评测。", "执行 Python 编译检查。", "安装并启动 systemd 服务。", "查看服务状态和 journal 日志。", "有摄像头时先做单独读帧。", "有扫码器时先验证原始文本。", "记录 CPU、内存、设备和测试结果。"], "形成可以交给控制组进行 9 月联调的树莓派版本。", ["树莓派测试全部通过。", "systemd 服务正常。", "模拟任务完成。", "设备单测通过或明确记录未到位设备。", "最终报告和联调清单完整。"], ["最终测试报告。", "树莓派部署记录。", "控制组接口清单。", "docs/progress/day14.md。"], "source .venv/bin/activate\npython -m tools.启动检查 --json\npython -m unittest discover -s tests -v\npython -m tools.离线评测 --output logs/final_evaluation.json\npython -m compileall -q app mission protocol vision tools tests\nsudo systemctl restart 视觉模块仿真服务.service\nsudo systemctl status 视觉模块仿真服务.service"),
    ]
    for day in days:
        add_day(doc, *day)

    add_heading(doc, "三、14 天结束后的交付物", level=1)
    add_bullets(doc, [
        "源码和标准目录结构。",
        "requirements.txt、树莓派安装步骤和启动脚本。",
        "颜色检测、AprilTag、扫码、状态机和协议接口说明。",
        "任务状态转换图、通信帧格式、命令字和错误码说明。",
        "自动化测试结果和离线评测报告。",
        "正常流程、故障流程和树莓派运行日志。",
        "尚未完成的真实硬件问题清单。",
    ])
    add_heading(doc, "四、9 月控制联调前必须确认", level=1)
    add_bullets(doc, [
        "串口号、波特率、数据位、停止位和校验方式。",
        "协议版本、帧格式、命令字、ACK/DONE 时序和错误码。",
        "坐标单位、坐标原点、坐标正方向和工位坐标。",
        "AprilTag ID、工位名称、物料编号、颜色名称和环码。",
        "抓取成功、限位、ToF 的有效范围和安全动作。",
        "通信断开和安全停止后的恢复方式。",
    ])
    add_heading(doc, "五、建议联调顺序", level=1)
    add_code(doc, "串口连接\n-> PING\n-> 单条移动指令\n-> 单条停止指令\n-> 摄像头画面\n-> 单个目标识别\n-> 单个 AprilTag 识别\n-> 单次抓取确认\n-> 单个物料流程\n-> 完整任务流程")

    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build()
