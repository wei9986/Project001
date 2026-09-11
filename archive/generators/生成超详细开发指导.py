# -*- coding: utf-8 -*-
"""生成适合零基础执行的 14 天视觉模块开发指导。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "14天树莓派视觉模块开发计划_详细执行版.docx"


def set_font(run, size=10.5, bold=False, color=None):
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)


def shade(cell, fill):
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def set_cell_text(cell, text, bold=False, fill=None):
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(text)
    set_font(run, 9.5, bold=bold)
    if fill:
        shade(cell, fill)


def add_label(doc, label, text):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(4)
    lead = paragraph.add_run(label + "：")
    set_font(lead, 10.5, bold=True, color=(31, 78, 121))
    body = paragraph.add_run(text)
    set_font(body, 10.5)
    return paragraph


def add_code(doc, command):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.65)
    paragraph.paragraph_format.space_after = Pt(4)
    run = paragraph.add_run(command)
    set_font(run, 9, color=(40, 40, 40))
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), "F2F2F2")
    paragraph._p.get_or_add_pPr().append(shading)


def add_bullet(doc, text):
    paragraph = doc.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(2)
    set_font(paragraph.add_run(text), 10.5)


def add_number(doc, text):
    paragraph = doc.add_paragraph(style="List Number")
    paragraph.paragraph_format.space_after = Pt(3)
    set_font(paragraph.add_run(text), 10.5)


def heading(doc, text, level=1):
    paragraph = doc.add_paragraph(style=f"Heading {level}")
    paragraph.paragraph_format.keep_with_next = True
    run = paragraph.add_run(text)
    set_font(run, 16 if level == 1 else 13, bold=True, color=(31, 78, 121))
    return paragraph


def add_day(doc, day):
    heading(doc, f"Day {day['day']} | {day['priority']} | {day['title']}", 2)
    add_label(doc, "今天只解决什么", day["goal"])
    add_label(doc, "为什么先做它", day["why"])
    add_label(doc, "开始前必须满足", day["before"])
    add_label(doc, "需要准备", day["materials"])

    paragraph = doc.add_paragraph()
    set_font(paragraph.add_run("按顺序操作"), 11, bold=True, color=(31, 78, 121))
    for step in day["steps"]:
        add_number(doc, step)

    paragraph = doc.add_paragraph()
    set_font(paragraph.add_run("当天命令"), 11, bold=True, color=(31, 78, 121))
    for command in day["commands"]:
        add_code(doc, command)

    add_label(doc, "你应该看到什么", day["expected"])
    add_label(doc, "通过标准", day["pass"])
    add_label(doc, "当天要留下什么", day["output"])

    paragraph = doc.add_paragraph()
    set_font(paragraph.add_run("卡住时按这个顺序排查"), 11, bold=True, color=(192, 80, 77))
    for item in day["debug"]:
        add_bullet(doc, item)

    add_label(doc, "今天不要做", day["avoid"])
    add_label(doc, "记录模板", day["record"])


def build_document():
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2.15)
    section.right_margin = Cm(2.15)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(5)
    for name in ("Heading 1", "Heading 2", "Heading 3"):
        style = doc.styles[name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    code_style = doc.styles.add_style("代码块", WD_STYLE_TYPE.PARAGRAPH)
    code_style.font.name = "Consolas"
    code_style.font.size = Pt(9)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(24)
    title.paragraph_format.space_after = Pt(10)
    set_font(title.add_run("14 天树莓派视觉模块开发计划"), 24, bold=True, color=(31, 78, 121))
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(24)
    set_font(subtitle.add_run("超详细执行版 | 面向零基础开发者 | 先完成关键风险最高的部分"), 12, color=(89, 89, 89))
    add_label(doc, "使用规则", "每天只完成当天任务。没有达到当天通过标准，就先排查并补齐，不跳到下一天。所有命令均在项目根目录 Project001-opencv.gcs 的 CMD 终端运行。")
    add_label(doc, "本计划的目标", "14 天后你应拥有一条可复现的视觉流程：相机画面 -> 颜色目标 -> 世界坐标 -> 稳定确认 -> 工位识别 -> 任务状态机 -> 串口仿真，并能用日志解释每一次失败。")
    add_label(doc, "不是目标", "这 14 天不追求立刻训练 YOLO，不追求一次解决所有光照问题，也不在没有测量数据时承诺抓取精度。先让基础链路可靠，再考虑模型升级。")

    heading(doc, "一、先认识项目", 1)
    add_label(doc, "项目根目录", "视觉模块开发\\Project001-opencv.gcs。请先在 VS Code 中打开这个文件夹，不要只打开某一个 Python 文件。")
    add_label(doc, "中文模块名", "项目已经使用中文模块名。例如颜色追踪是 vision.颜色追踪，启动检查是 tools.启动检查，应用仿真是 app.视觉应用。输入命令时不要再使用旧的英文模块名。")
    add_label(doc, "最重要的三条命令", "第一条检查环境，第二条检查代码没有被改坏，第三条检查完整仿真流程。每次改动配置或代码后，至少运行前两条。")
    add_code(doc, "..\\.venv\\Scripts\\python.exe -m tools.启动检查 --json")
    add_code(doc, "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v")
    add_code(doc, "..\\.venv\\Scripts\\python.exe -m app.视觉应用 --mode simulation --log logs\\当天应用仿真.jsonl")

    heading(doc, "二、开发优先级和完成边界", 1)
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    headers = ["优先级", "模块", "为什么先做", "完成标志"]
    for cell, text in zip(table.rows[0].cells, headers):
        set_cell_text(cell, text, bold=True, fill="D9EAF7")
    rows = [
        ("P0", "真实画面与颜色识别", "后续所有功能都依赖稳定画面和正确目标。", "真实物料能识别，错误场景会拒绝。"),
        ("P1", "标定与世界坐标", "机器人不能直接拿像素坐标抓取。", "能输出 world_mm，并有误差记录。"),
        ("P2", "精定位与失效处理", "错误或旧坐标会导致危险动作。", "跳变、丢失目标不会触发抓取。"),
        ("P3", "AprilTag 工位定位", "颜色不能说明目标属于哪个工位。", "Tag ID 能稳定映射到站点。"),
        ("P4", "任务与串口接口", "视觉结果必须以固定格式交给控制组。", "ACK、DONE、FAIL 和重试可仿真。"),
        ("P5", "树莓派部署与交付", "比赛现场需要可启动、可检查、可复现。", "断电重启后能自检并留下日志。"),
    ]
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            set_cell_text(cell, text)

    heading(doc, "三、每天都要遵守的工作方法", 1)
    methods = [
        "一次只改一个变量。比如先只改 min_area，不要同时改 HSV、面积和确认帧数。",
        "每次改动前复制配置文件或记录旧值。没有“修改前”和“修改后”的比较，不能判断改动是否有效。",
        "优先保存原始画面、掩膜、JSONL 日志和测试输出。截图比“我觉得可以”更有用。",
        "先确认输入存在，再怀疑算法。先看相机是否读到画面，再看掩膜，再看目标是否 valid。",
        "遇到异常先缩小问题：只跑一项单元测试、只用一张图片、只识别一种颜色、只发送一条串口命令。",
        "不伪造通过结果。没有相机、树莓派或串口硬件时，明确记录“未进行真机验证”，但可以完成仿真验证。",
    ]
    for item in methods:
        add_bullet(doc, item)

    heading(doc, "四、统一调试路线", 1)
    add_label(doc, "当程序没有识别到目标", "按相机是否打开 -> 画面是否正常 -> HSV 掩膜是否有白色目标 -> 轮廓面积是否达到 min_area -> 目标是否连续多帧 -> valid 是否为 true 的顺序查。")
    add_label(doc, "当任务不往下走", "看 logs 中最后一条 action，再查看状态机正在等待什么事件。不要看到 complete 没出现就直接改状态机。")
    add_label(doc, "当坐标不可信", "先停止抓取。检查相机是否移动、分辨率是否变化、平面四点顺序是否一致、标定图是否清晰，再重新测量。")
    add_label(doc, "当串口动作卡住", "先区分没有收到 ACK、收到 ACK 但没有 DONE、收到 FAIL。ACK 只表示控制器已收到，不表示动作完成。")

    days = [
        {
            "day": 1, "priority": "基础", "title": "确认环境、中文模块名和项目基线",
            "goal": "确认电脑能够运行项目，且你知道“预检、测试、仿真”三者分别检查什么。",
            "why": "没有基线时，后面任何报错都无法判断是你新改出的，还是环境本来就有问题。",
            "before": "VS Code 已打开项目根目录；终端提示符末尾是 Project001-opencv.gcs。",
            "materials": "电脑、VS Code、项目虚拟环境。今天不需要相机或树莓派。",
            "steps": ["在 VS Code 终端输入 cd，确认当前目录是项目根目录。", "运行启动检查，记录 JSON 中 passed、python、optional_missing_modules 三个字段。", "运行全部测试。不要只看最后一行，要确认所有测试项都是 ok。", "运行应用仿真并指定单独日志文件。", "用 type 命令查看日志最后几行，确认最后动作是 task_complete。", "创建 docs\\progress\\第1天.md，写下今天运行的三条命令和结果。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m tools.启动检查 --json", "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v", "..\\.venv\\Scripts\\python.exe -m app.视觉应用 --mode simulation --log logs\\day01_application.jsonl", "type logs\\day01_application.jsonl"],
            "expected": "预检返回 passed: true；测试末尾显示 OK；应用仿真输出 state: complete。",
            "pass": "三项结果都成功，且你能说出日志中 S0_init、S1_read_task、complete 分别是什么。",
            "output": "第1天记录、day01_application.jsonl。",
            "debug": ["出现“找不到 python.exe”：先确认当前目录，再使用 ..\\.venv\\Scripts\\python.exe。", "CMD 中不要输入 PowerShell 专用的 &。", "serial 缺失是仿真阶段可选提示，不是失败；只有接真实串口时才需要安装。"],
            "avoid": "不要先安装新库、不要先改代码、不要因为日志重复就删除旧日志。",
            "record": "日期；预检 passed；测试数量；仿真 state；今天遇到的第一个问题。",
        },
        {
            "day": 2, "priority": "P0", "title": "让真实相机稳定提供画面",
            "goal": "让 USB 相机或电脑摄像头连续读取画面，并能正常退出。",
            "why": "没有稳定输入，HSV、标定和 AprilTag 都无从谈起。",
            "before": "Day 1 通过；准备 USB 相机或笔记本摄像头；关闭占用相机的微信、会议软件和浏览器网页。",
            "materials": "相机、红绿蓝物料、单色背景、固定支架。",
            "steps": ["从 0 号相机开始运行颜色追踪预览。", "画面出现后，依次放入红、绿、蓝物料，观察是否显示标注。", "保持运行 10 分钟，每隔 1 分钟移动一次物料。", "记录画面分辨率、相机编号、安装高度、是否自动曝光。", "若 source 0 打不开，依次尝试 1 和 2；每次只改 source。", "按 q 退出窗口，确认终端没有报错。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m vision.颜色追踪 --source 0 --config config\\颜色追踪配置.json --colors red green blue --log logs\\day02_tracking.jsonl"],
            "expected": "窗口持续显示实时画面；退出后 logs\\day02_tracking.jsonl 中存在逐帧记录。",
            "pass": "连续读取 10 分钟不黑屏、不崩溃；三种物料至少各出现一次检测记录。",
            "output": "相机参数记录、一张原始截图、一份追踪 JSONL。",
            "debug": ["相机打不开：检查其他软件是否占用，再换编号。", "画面全黑：检查系统相机权限、镜头盖和 USB 连接。", "有画面但没有框：先留到 Day 4 检查，不要今天就乱调全部 HSV。"],
            "avoid": "不要在相机未固定时采集后续样本；不要把自动曝光造成的亮度跳变当作算法错误。",
            "record": "相机型号；source 编号；分辨率；安装高度；10 分钟是否稳定。",
        },
        {
            "day": 3, "priority": "P0", "title": "建立真实样本和失败场景清单",
            "goal": "收集足够的真实画面，让后续调参有依据。",
            "why": "不先看真实失败场景，参数只能在一张“漂亮图片”上看起来有效。",
            "before": "Day 2 已能稳定读帧；相机位置已临时固定。",
            "materials": "红绿蓝物料、白色和深色背景、补光灯或不同室内光源、尺子。",
            "steps": ["在 data 下创建 data\\field_samples\\day03 文件夹。", "每种颜色至少拍正面、30 度倾斜、远处、近处、局部遮挡五类画面。", "额外拍强反光、阴影、无物料、两个同色物料四类失败画面。", "文件名写清颜色和场景，例如 red_front_01.jpg。", "建立样本清单，记录文件名、颜色、距离、光照、理论上应识别还是应拒绝。", "随机抽查 10 张图片，确认别人只看文件名和清单也能理解。"],
            "commands": ["mkdir data\\field_samples\\day03", "dir data\\field_samples\\day03"],
            "expected": "每种颜色都有多角度样本；失败样本不是空白，而是能说明问题的画面。",
            "pass": "至少 30 张有效样本，三种颜色齐全，且至少有 4 类失败场景。",
            "output": "样本目录、样本清单、三张最难样本截图。",
            "debug": ["样本太少：先补拍，不要急着训练 YOLO。", "文件看不出内容：立刻重命名，不要依赖记忆。", "画面差异很小：改变距离、背景或光线，避免收集重复图。"],
            "avoid": "不要修改已有 data\\shapes_dataset；那是训练示例数据，不是今天的真实采集目录。",
            "record": "样本总数；每种颜色数量；失败场景数量；最难的三个文件名。",
        },
        {
            "day": 4, "priority": "P0", "title": "在真实画面上建立颜色识别基线",
            "goal": "先测量现在的识别表现，不改参数。",
            "why": "没有修改前基线，Day 5 的调参无法证明变好。",
            "before": "Day 3 样本与清单已完成。",
            "materials": "Day 3 样本、颜色追踪配置、记录表。",
            "steps": ["打开 vision\\颜色追踪.py，先只阅读模块顶部中文说明和 track 方法。", "打开 config\\颜色追踪配置.json，找到 red、green、blue 的 ranges 和 tracking 参数。", "按样本清单逐张或逐段播放画面，记录正确、漏检、误检、错分、坐标跳变。", "每次都记录 targets 中 valid 是否为 true。", "从每类失败中选一张代表样本，写下你对原因的猜测。", "统计总样本数和五类结果数量，不追求今天达到最高准确率。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m unittest tests.test_颜色追踪 -v", "..\\.venv\\Scripts\\python.exe -m tools.仿真验证 --frames 30 --packets 20 --output logs\\day04_simulation.json"],
            "expected": "你能解释 valid=false 的含义：视觉结果还不能交给抓取动作。",
            "pass": "完成一份基线统计，且每个失败分类都有代表样本或明确写“本次未出现”。",
            "output": "day04.md、基线统计表、代表截图路径。",
            "debug": ["完全没框：先确认画面里真的有物料，再看 HSV 掩膜。", "框很乱：先记录噪声来源，明天只调一个参数。", "valid 一直 false：检查连续确认帧数，不要立刻把它改成 1。"],
            "avoid": "不要今天同时修改 HSV、min_area 和 confirm_frames。",
            "record": "样本数；正确/漏检/误检/错分/跳变；最严重问题；截图路径。",
        },
        {
            "day": 5, "priority": "P0", "title": "用单变量实验得到第一版参数",
            "goal": "用同一批样本对比参数，得到能解释的第一版配置。",
            "why": "调参不是凭感觉移动滑块，而是比较一次变化带来的收益和代价。",
            "before": "Day 4 已有基线；已经复制一份配置备份。",
            "materials": "基线样本、备份配置、参数记录表。",
            "steps": ["复制颜色配置为 logs\\day05_颜色配置_备份.json。", "实验 A：只改 min_area，记录小噪声和远处目标的变化。", "恢复备份。实验 B：只改 confirm_frames，记录闪烁减少量和确认延迟。", "恢复备份。实验 C：只改 max_lost_frames，记录短暂遮挡和旧目标保留。", "选出总体退化最少的一组，不是只看最好的一张图。", "运行全部测试，确认配置改动没有破坏已有逻辑。"],
            "commands": ["copy config\\颜色追踪配置.json logs\\day05_颜色配置_备份.json", "..\\.venv\\Scripts\\python.exe -m tools.参数调试 --source 0 --config config\\颜色追踪配置.json --color red", "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v"],
            "expected": "你能说清 min_area、confirm_frames、max_lost_frames 各自解决什么问题和会带来什么副作用。",
            "pass": "有三次单变量实验记录，最终配置能通过全部测试。",
            "output": "最终配置、备份配置、三份实验记录、day05.md。",
            "debug": ["掩膜太大：优先缩小 HSV 范围或提高 S/V 下限。", "掩膜正确却没目标：检查 min_area、圆度和实心度。", "改完更差：恢复备份，不要在坏配置上继续叠加。"],
            "avoid": "不要只用一个成功样本决定参数；不要删除备份。",
            "record": "参数名；旧值；新值；样本数；正确/漏检/误检；最终结论。",
        },
        {
            "day": 6, "priority": "P1", "title": "采集棋盘格并完成相机内参标定",
            "goal": "生成与正式相机、正式分辨率匹配的相机标定文件。",
            "why": "镜头畸变和相机内参不正确时，像素到世界坐标会整体偏移。",
            "before": "相机安装位置和分辨率已固定；棋盘格内角点数量和每格边长已确认。",
            "materials": "打印平整的棋盘格、尺子、固定相机、至少 15 张标定照片。",
            "steps": ["在 data\\calibration 下保存棋盘格照片。", "让棋盘格覆盖中心、四角和不同倾斜角度；不要只在画面中央拍。", "删除模糊、过曝、棋盘格太小的照片。", "确认 board-width 与 board-height 指内角点，不是方格数。", "用真实 square-size 运行标定命令。", "保存原始照片和命令；相机重新安装后需要重新标定。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m vision.相机标定 --images data\\calibration\\*.jpg --board-width 9 --board-height 6 --square-size 25 --output config\\相机标定.json", "..\\.venv\\Scripts\\python.exe -m unittest tests.test_相机标定 -v"],
            "expected": "生成标定 JSON，并在终端看到重投影 RMS 误差。",
            "pass": "标定文件可生成；相同分辨率下测试通过；你保留了棋盘格原图。",
            "output": "data\\calibration、config\\相机标定.json、day06.md。",
            "debug": ["找不到角点：先核对内角点数、照片清晰度、反光和棋盘格占画面比例。", "照片数量足够但误差大：增加四角和倾斜照片，删除模糊图。", "分辨率不匹配：不要强行复用，改回标定时分辨率或重做标定。"],
            "avoid": "不要手改相机矩阵；不要把不同分辨率的照片混在一起。",
            "record": "棋盘格规格；照片数量；有效照片数；RMS 误差；相机分辨率。",
        },
        {
            "day": 7, "priority": "P1", "title": "把像素坐标转换为场地毫米坐标",
            "goal": "建立清晰的世界坐标原点、方向和误差记录。",
            "why": "机器人需要的是毫米坐标，不是画面中的 x/y 像素。",
            "before": "Day 6 标定成功；正式工位和相机位置不再移动。",
            "materials": "固定平面标定板、尺子、至少 4 个已知点、记录表。",
            "steps": ["在 day07.md 画出原点、x 轴正方向、y 轴正方向。", "选择 4 个共面的已知点，例如左上、右上、右下、左下。", "用同一顺序记录 image_points 与 world_points；单位统一 mm。", "调用平面映射，确认结果可输出 world_mm。", "再选择中心和边缘至少 5 个验证点，记录预测值、尺子实测值和误差。", "若误差超出团队容许范围，先检查安装和点顺序，再考虑重标定。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m unittest tests.test_相机标定 -v", "..\\.venv\\Scripts\\python.exe -m tools.仿真验证 --frames 30 --packets 20 --output logs\\day07_mapping_check.json"],
            "expected": "颜色目标结构化结果中出现 world_mm，且验证点误差有记录。",
            "pass": "至少 5 个验证点；原点和方向写清楚；没有用像素坐标代替世界坐标下发抓取。",
            "output": "平面点 JSON、误差表、坐标示意图、day07.md。",
            "debug": ["坐标翻转或严重变形：检查四个点是否按同一方向录入。", "全部点偏同一方向：检查相机是否移动或世界原点定义是否错。", "只有边缘误差大：检查标定照片和镜头畸变处理。"],
            "avoid": "不要估算世界点；不要在相机移动后继续沿用旧映射。",
            "record": "原点定义；点顺序；5 个验证误差；最大误差；是否允许抓取。",
        },
        {
            "day": 8, "priority": "P2", "title": "确认稳定目标才允许抓取",
            "goal": "验证目标从候选到稳定、跳变被拒绝、丢失后不再使用旧坐标。",
            "why": "比赛中最危险的不是没有抓取，而是对错误或过期坐标执行抓取。",
            "before": "Day 7 能输出 world_mm。",
            "materials": "固定物料、可移动物料、Day 5 配置、日志文件。",
            "steps": ["阅读 vision\\精定位.py 顶部中文说明，重点看 stable、lost、timeout。", "固定物料，观察连续多帧后何时变为稳定。", "快速移动物料，故意制造坐标跳变，确认模块不输出可抓取结果。", "把物料移出画面，确认丢失后不再复用旧 world_mm。", "记录稳定需要的帧数和大致时间。", "运行对应专项测试。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m unittest tests.test_第二阶段流程.Stage2Tests.test_fine_localization_rejects_jump_and_confirms_stable_mean -v"],
            "expected": "稳定时才得到可执行坐标；跳变和丢失时得到拒绝或恢复信号。",
            "pass": "稳定、跳变、丢失三种场景均有日志或截图证据。",
            "output": "day08.md、三种场景记录、稳定帧数记录。",
            "debug": ["一直不稳定：先检查相机固定、照明、Day 5 参数，再调精定位阈值。", "跳变仍通过：核对 max_step_mm 和样本数量，补充单元测试。", "丢失后仍有旧坐标：停止集成，先修复失效状态。"],
            "avoid": "不要为了更快抓取把确认帧数随意降到 1。",
            "record": "稳定所需帧数；跳变是否拒绝；丢失是否清空 world_mm；结论。",
        },
        {
            "day": 9, "priority": "P2", "title": "建立视觉失败案例库",
            "goal": "把“识别失败”变成可复现、可分类、可继续修复的案例。",
            "why": "案例库能防止你每次调参都忘记以前修过的问题。",
            "before": "P0 和 P1 已完成基础验证。",
            "materials": "Day 3 样本、Day 8 三种场景、失败记录模板。",
            "steps": ["对每个失败样本运行识别，记录实际结果和期望结果。", "把失败分类为漏检、误检、错分、坐标跳变、安全拒绝、硬件输入异常。", "同色多目标时记录 target_id 数量和控制端应如何选择。", "遮挡和无目标时检查 valid，而不是只看窗口有没有框。", "每个案例写“输入条件 -> 实际结果 -> 期望结果 -> 下次处理”。", "选择对比赛影响最大的一个问题作为后续优化目标。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m tools.离线评测 --output logs\\day09_fault_review.json"],
            "expected": "离线评测报告覆盖正常、丢目标、丢 Tag、错扫码、断串口场景。",
            "pass": "至少 10 个失败案例，每个都能复现或明确说明硬件条件。",
            "output": "failures.md、案例截图或视频路径、day09.md。",
            "debug": ["没有失败案例：故意制造遮挡、反光、无目标、同色双目标。", "所有失败都写“算法不好”：必须具体到输入条件和结果字段。", "安全拒绝被当作错误：先判断拒绝是否符合设计。"],
            "avoid": "不要一天同时修复所有案例；先选最影响任务完成率的一项。",
            "record": "案例编号；输入；实际；期望；分类；下一步；是否已复现。",
        },
        {
            "day": 10, "priority": "P3", "title": "识别 AprilTag 并建立工位映射",
            "goal": "让 Tag ID 稳定映射到团队约定的工位名称。",
            "why": "颜色识别告诉你物料是什么，AprilTag 才告诉你它处于哪个工位。",
            "before": "相机画面正常；准备打印清晰的 AprilTag；已确定 Tag 字典、ID 和物理边长。",
            "materials": "AprilTag、尺子、工位名称表、config\\站点标签.json。",
            "steps": ["打开 config\\站点标签.json，核对每个 ID 与工位名称。", "正对相机放一个 Tag，先验证能识别 ID。", "测试未知 ID、无 Tag、两个 Tag 三种情况。", "记录每种情况 stations 输出内容，不只记录成功场景。", "把 Tag 字典、尺寸、识别距离和相机位置写入 day10.md。", "明确颜色目标与 Tag 工位是两条独立结果，不能互相代替。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m unittest tests.test_第二阶段流程.Stage2Tests.test_apriltag_station_detection_works_without_camera_calibration -v"],
            "expected": "已配置 Tag 映射为站点；未知 Tag 不产生错误工位。",
            "pass": "正常、未知、丢失、多 Tag 四类情况都有记录，且没有错误映射。",
            "output": "站点标签配置、Tag 测试记录、day10.md。",
            "debug": ["完全识别不到：检查打印清晰度、反光、Tag 字典和画面中 Tag 尺寸。", "识别到错误 ID：检查打印文件和字典，不要先改状态机。", "多个 Tag 混乱：先保留全部检测结果，再定义上层选择规则。"],
            "avoid": "不要用颜色结果伪造工位；不要随意改变已约定的站点字段名。",
            "record": "Tag 字典；ID；站点；边长；距离；正常/异常结果。",
        },
        {
            "day": 11, "priority": "P3", "title": "把颜色目标、坐标和工位接入运行循环",
            "goal": "在同一轮运行中同时得到目标、world_mm、站点和任务动作。",
            "why": "单个模块能运行不等于集成后能推进任务。",
            "before": "Day 5、Day 7、Day 8、Day 10 结果都有记录。",
            "materials": "当前配置、仿真日志目录、运行循环源码。",
            "steps": ["阅读 app\\运行循环.py 顶部中文说明和 step 方法。", "在运行循环中确认数据流是相机 -> 目标与站点 -> 安全门 -> 状态机。", "运行应用仿真并同时查看动作 JSONL。", "查看最后几条日志，确认任务动作按预期顺序出现。", "若任务不动，按画面、target valid、station 数量、pending action、安全决定顺序排查。", "将一次完整运行的日志路径写进 day11.md。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m app.视觉应用 --mode simulation --log logs\\day11_application.jsonl", "type logs\\day11_application.jsonl"],
            "expected": "日志从 self_test 开始，最终以 task_complete 结束。",
            "pass": "你能从日志解释每一个动作为什么出现，而不是只确认 complete。",
            "output": "day11_application.jsonl、day11.md、最后十行日志截图。",
            "debug": ["没有 target：回到 Day 2 到 Day 5。", "没有 station：回到 Day 10。", "有视觉结果却不动：查看 pending_actions 和安全 decision。", "状态错误：在 _dispatch 或状态机 handle 处打断点。"],
            "avoid": "不要在运行循环中直接写电机控制代码；硬件完成后应以事件确认。",
            "record": "最后状态；最后动作；目标数量；站点数量；卡点；日志路径。",
        },
        {
            "day": 12, "priority": "P4", "title": "冻结视觉与控制接口，验证串口协议",
            "goal": "与控制组确认字段、单位、动作确认时序和故障码。",
            "why": "接口不冻结，视觉和控制两边都会反复返工。",
            "before": "Day 7、Day 8、Day 10、Day 11 已有实际输出记录。",
            "materials": "接口清单、控制组成员、串口协议模块、内存串口仿真。",
            "steps": ["整理接口字段：target_id、color、pixel、world_mm、valid、confidence、station_id。", "明确坐标单位为 mm、原点位置、x/y 正方向、时间字段单位。", "阅读 protocol\\串口协议.py，确认帧结构、序号、CRC、ACK、DONE、FAIL。", "用内存串口运行正常、丢 ACK、丢 DONE、FAIL 的测试。", "与控制组确认：视觉不能在只有 ACK 时发送下一条运动命令。", "把冻结版本写入接口文档，并标记负责人和日期。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m unittest tests.test_第二阶段流程.Stage2Tests.test_protocol_ack_done_retry_and_heartbeat -v", "..\\.venv\\Scripts\\python.exe -m unittest tests.test_第二阶段流程.Stage2Tests.test_protocol_action_bridge_completes_over_memory_serial -v"],
            "expected": "正常命令在 DONE 后完成；丢包会重试；FAIL 会保留错误状态。",
            "pass": "控制组和视觉组对字段、单位、ACK/DONE 含义没有歧义。",
            "output": "接口文档、协议测试结果、day12.md。",
            "debug": ["帧解析失败：先打印十六进制字节和 invalid_frames。", "收到 ACK 后卡住：检查是否缺少 DONE。", "命令冲突：检查是否有未完成 pending command。"],
            "avoid": "不要让两个组各自定义坐标原点；不要把 ACK 当作动作完成。",
            "record": "接口版本；字段；单位；负责人；协议测试；未决问题。",
        },
        {
            "day": 13, "priority": "P5", "title": "部署到树莓派并完成启动检查",
            "goal": "在树莓派上建立独立、可重复的运行环境。",
            "why": "电脑能运行不代表树莓派能运行；性能、相机编号和依赖都不同。",
            "before": "P0-P4 仿真通过；树莓派、存储卡、电源、网络和相机已准备。",
            "materials": "树莓派、稳定电源、相机、网络、项目代码副本。",
            "steps": ["确认树莓派系统版本、Python 版本和可用磁盘空间。", "创建项目虚拟环境并安装 requirements.txt。", "运行中文模块启动检查，先解决依赖和配置缺失。", "在树莓派上列出相机设备并确认实际编号。", "先运行应用仿真，再运行真实相机预览；两个步骤分开记录。", "记录 CPU 占用、帧率、温度和连续运行时间。"],
            "commands": ["python3 -m venv .venv", "source .venv/bin/activate", "python -m pip install -r requirements.txt", "python -m tools.启动检查 --json", "python -m app.视觉应用 --mode simulation --log logs/day13_raspberry_pi.jsonl"],
            "expected": "树莓派预检通过，应用仿真完成，日志可写入本地目录。",
            "pass": "明确区分“仿真通过”和“真实相机通过”；任何未到位硬件都写入风险清单。",
            "output": "树莓派环境记录、预检输出、性能记录、day13.md。",
            "debug": ["缺少 serial：仅在真串口阶段安装 pyserial。", "相机编号不同：以树莓派实际设备为准。", "性能不足：先降低分辨率或关闭不必要窗口，再评估算法优化。"],
            "avoid": "不要在系统 Python 中随意 sudo pip 安装；不要用不稳定充电宝代替电源。",
            "record": "系统；Python；相机编号；帧率；温度；连续运行时间；未解决风险。",
        },
        {
            "day": 14, "priority": "P5", "title": "完成演练、离线评测和最终交付",
            "goal": "把零散模块变成一套能演示、能定位问题、能交给队友继续维护的成果。",
            "why": "比赛现场真正需要的是可复现流程和故障处理方式，不是单次成功截图。",
            "before": "Day 1 到 Day 13 的记录、配置、样本和日志都可找到。",
            "materials": "完整项目、树莓派或电脑、真实物料、Tag、接口文档、检查表。",
            "steps": ["从零开始运行预检、全部测试、离线评测、应用仿真。", "按真实任务顺序演练：启动、自检、读任务、识别物料、确认工位、动作确认、回零。", "故意制造一个故障，例如移走物料、遮挡 Tag 或断开串口，确认系统进入拒绝或恢复。", "整理最终配置、标定文件、接口版本、失败案例库和日志目录。", "写 final_report.md：已验证内容、未验证硬件、最大风险、下一步负责人。", "让一名队友按照你的文档独立运行一次；他卡住的位置就是文档还需补充的位置。"],
            "commands": ["..\\.venv\\Scripts\\python.exe -m tools.启动检查 --json", "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v", "..\\.venv\\Scripts\\python.exe -m tools.离线评测 --output logs\\final_evaluation.json", "..\\.venv\\Scripts\\python.exe -m app.视觉应用 --mode simulation --log logs\\final_application.jsonl", "..\\.venv\\Scripts\\python.exe -m compileall -q app mission protocol vision tools tests"],
            "expected": "预检、测试、离线评测和仿真均成功；故障场景不会输出伪造的有效坐标。",
            "pass": "新同学能按文档复现流程；你能用日志说明正常与故障的差异。",
            "output": "final_report.md、最终评测、最终仿真日志、交付清单、day14.md。",
            "debug": ["最后失败：不要临时改多个参数，回到对应 Day 的记录定位。", "演练与测试结果不同：保留两份日志，先比较输入和配置。", "队友无法复现：补充缺少的路径、命令或硬件条件。"],
            "avoid": "不要为了演示隐藏失败；不要删除原始日志和失败样本。",
            "record": "验证清单；故障演练结果；未验证项；风险等级；下一位负责人。",
        },
    ]
    heading(doc, "五、每日执行卡", 1)
    add_label(doc, "阅读方法", "每天先读“今天只解决什么”，再按“按顺序操作”执行。只有通过标准满足后才继续。若无法完成，至少把卡点、日志和下一步假设写入当天记录。")
    for day in days:
        add_day(doc, day)

    heading(doc, "六、VS Code 调试操作", 1)
    add_label(doc, "运行测试", "按 Ctrl+Shift+P，选择 Tasks: Run Task，再选择“运行全部单元测试”。也可以直接在 CMD 输入文档中的命令。")
    add_label(doc, "设置断点", "在 app\\运行循环.py 的 step、mission\\任务状态机.py 的 handle、vision\\颜色追踪.py 的 track 左侧单击行号。运行后观察当前 state、event、targets、stations、pending_actions。")
    add_label(doc, "不打断点也能排查", "查看 logs 中的 JSONL。每行是一条独立记录，按时间顺序看 state、action、data；最后一条通常最能说明卡在哪一步。")
    add_label(doc, "先看什么", "视觉问题看 frame、mask、targets.valid；流程问题看 state、event、action；协议问题看 sequence、ACK、DONE、FAIL、invalid_frames。")

    heading(doc, "七、交付前检查清单", 1)
    checks = [
        "启动检查 passed 为 true。",
        "全部单元测试为 OK。",
        "颜色配置、标定文件、站点标签和任务配置均有明确版本。",
        "真实相机、树莓派、串口分别标明已验证或未验证。",
        "坐标原点、轴方向和单位均写清为 mm。",
        "至少保留一次正常日志和一次故障日志。",
        "控制组已确认 ACK、DONE、FAIL 的时序。",
        "队友可以按本文件从零复现一次。",
    ]
    for item in checks:
        add_bullet(doc, "[ ] " + item)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(footer.add_run("树莓派视觉模块 | 超详细执行版 | 以日志和测试为准"), 8, color=(127, 127, 127))
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
