from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "14天树莓派视觉模块开发计划_详细执行版.docx"


def set_font(run, size=10.5, color=None, bold=False):
    run.font.name = "Microsoft YaHei"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Calibri")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Calibri")
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def shade(cell, fill):
    props = cell._tc.get_or_add_tcPr()
    node = props.find(qn("w:shd"))
    if node is None:
        node = OxmlElement("w:shd")
        props.append(node)
    node.set(qn("w:fill"), fill)


def set_cell_width(cell, width):
    props = cell._tc.get_or_add_tcPr()
    node = props.find(qn("w:tcW"))
    if node is None:
        node = OxmlElement("w:tcW")
        props.append(node)
    node.set(qn("w:w"), str(width))
    node.set(qn("w:type"), "dxa")
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def add_text(cell, text, bold=False, color=None, size=9):
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(text)
    set_font(r, size=size, color=color, bold=bold)


def add_table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.style = "Table Grid"
    for cell, label, width in zip(table.rows[0].cells, headers, widths):
        set_cell_width(cell, width)
        shade(cell, "1F4E78")
        add_text(cell, label, bold=True, color="FFFFFF")
    for row in rows:
        cells = table.add_row().cells
        for cell, value, width in zip(cells, row, widths):
            set_cell_width(cell, width)
            add_text(cell, value)
    doc.add_paragraph()
    return table


def heading(doc, text, level=1):
    p = doc.add_heading(level=level)
    r = p.add_run(text)
    set_font(r, size={1: 16, 2: 13, 3: 11}[level], color="1F4E78", bold=True)
    p.paragraph_format.space_before = Pt(14 if level == 1 else 9)
    p.paragraph_format.space_after = Pt(6)


def bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    set_font(p.add_run(text))


def labeled(doc, label, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    set_font(p.add_run(f"{label}："), color="1F4E78", bold=True)
    set_font(p.add_run(text))


def code(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.25)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text)
    r.font.name = "Consolas"
    r._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Consolas")
    r.font.size = Pt(9)


def numbered(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(3)
    set_font(p.add_run(text))


def add_day_guide(doc, day, priority, title, preparation, steps, success, troubleshooting, output, commands):
    heading(doc, f"Day {day} | {priority} | {title}", level=2)
    labeled(doc, "开始前", preparation)
    for step in steps:
        numbered(doc, step)
    labeled(doc, "通过标准", success)
    labeled(doc, "卡住时先查", troubleshooting)
    labeled(doc, "当天交付", output)
    for command in commands:
        code(doc, command)


def build():
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.2

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(8)
    set_font(p.add_run("14 天树莓派视觉模块开发计划"), size=24, color="0B2545", bold=True)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(18)
    set_font(p.add_run("工创智能物流机器人 | 按关键工作量、风险与依赖排序"), size=12, color="5B6470")

    callout = doc.add_table(rows=1, cols=1)
    cell = callout.cell(0, 0)
    shade(cell, "EAF2F8")
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    set_font(p.add_run("执行原则："), color="1F4E78", bold=True)
    set_font(p.add_run("先完成高工作量、高风险、且被后续模块依赖的视觉主链路。每项未达到验收条件，不进入下一优先级。YOLO 不作为前置任务，只有传统视觉无法满足实际物料识别时才启动。"))

    heading(doc, "一、开发优先级与边界")
    add_table(
        doc,
        ["优先级", "模块", "排序原因", "阶段交付"],
        [
            ("P0", "真实相机输入与物料识别", "工作量最大、现场不确定性最高，后续所有视觉任务依赖它。", "稳定输出颜色、像素坐标、valid 和 confidence。"),
            ("P1", "相机标定与 world_mm", "机械抓取和工位协作都需要真实坐标。", "像素坐标转换为毫米坐标，并有误差记录。"),
            ("P2", "精定位与抗干扰", "决定系统是否能从“识别到”升级为“允许抓取”。", "跳变、遮挡、丢失时拒绝执行。"),
            ("P3", "AprilTag 工位定位", "决定机器人能否确认所在工位。", "Tag ID、工位名称和位姿结果可靠。"),
            ("P4", "任务码与接口契约", "工作量较小，依赖前面视觉结果稳定后再接入。", "视觉结果、任务字段和错误状态可对接。"),
            ("P5", "树莓派部署与联调验证", "依赖软件主链路和硬件到位。", "树莓派稳定运行并形成联调包。"),
            ("P6", "YOLO 条件开发", "数据、算力和真实失败案例不足时投入回报低。", "仅在 P0 无法达标时启动数据集与模型路线。"),
        ],
        [700, 1900, 3600, 3160],
    )

    heading(doc, "二、14 天总览")
    add_table(
        doc,
        ["天", "优先级", "当天主任务", "当天可验收结果"],
        [
            ("1", "基线", "环境、测试和模拟任务基线", "preflight 通过；28 项测试通过；模拟任务 complete。"),
            ("2", "P0", "接入真实或电脑摄像头，确认稳定读帧", "连续读帧 10 分钟，无崩溃；保存首批样本。"),
            ("3", "P0", "建立物料样本与测试场景清单", "红绿蓝物料的光照、背景、距离、角度样本计划完成。"),
            ("4", "P0", "用真实画面运行颜色检测基线", "每个物料都有识别结果；误检、漏检被记录。"),
            ("5", "P0", "单变量调 HSV、面积与确认帧参数", "得到可复现的参数文件和对比报告。"),
            ("6", "P1", "采集棋盘格并完成相机内参标定", "标定文件可加载，畸变校正正常。"),
            ("7", "P1", "建立像素到场地毫米坐标转换", "四角与中心点误差记录完成，输出 world_mm。"),
            ("8", "P2", "验证稳定确认、跳变拒绝和目标丢失", "不稳定目标不产生可抓取坐标。"),
            ("9", "P2", "进行光照、遮挡、多目标故障测试", "每类故障有预期处理结果和日志。"),
            ("10", "P3", "接入 AprilTag，建立 ID 到工位映射", "正常、未知、丢失、多 Tag 都有明确输出。"),
            ("11", "P3", "将 Tag 与目标检测串成工位内视觉闭环", "在固定工位输出“目标 + 工位 + 可信度”。"),
            ("12", "P4", "核对任务码、视觉结果和控制端接口", "确认字段、坐标系、单位、错误状态和时序。"),
            ("13", "P5", "在树莓派运行主链路和离线评测", "依赖检查、模拟任务、日志持续写入均通过。"),
            ("14", "P5", "回归、故障报告和控制组交付", "形成联调版本、测试报告、未完成硬件清单。"),
        ],
        [500, 900, 4000, 3960],
    )

    heading(doc, "三、逐阶段执行说明")
    phases = [
        ("P0：真实相机输入与物料识别（Day 2-5）", [
            "先用电脑 USB 摄像头完成读帧；树莓派相机到位后保持同样的 read() 接口替换。",
            "样本必须覆盖红、绿、蓝物料在不同光照、背景、距离、角度和遮挡条件下的画面。",
            "每次调参只改一个关键参数：HSV 范围、min_area、confirm_frames 或 max_lost_frames。",
            "验收门槛：连续 20 次固定工位识别中，记录成功、漏检、误检和未确认结果；没有数据不进入 P1。",
        ]),
        ("P1：标定与真实坐标（Day 6-7）", [
            "使用与正式运行相同的相机、分辨率、安装高度和焦距采集棋盘格。",
            "建立画面四角与场地四角的对应关系，输出 world_mm，不以像素坐标直接指导抓取。",
            "验收门槛：记录四角和中心点的坐标误差；超过团队允许误差时，先修安装与标定，不继续做机械抓取。",
        ]),
        ("P2：精定位与抗干扰（Day 8-9）", [
            "确认帧数不足、坐标跳变过大、目标连续丢失时，视觉模块必须输出不可执行状态。",
            "优先做失败场景，而不是只演示正常场景：反光、阴影、遮挡、两个同色目标和画面抖动。",
            "验收门槛：不存在“目标已经丢失，却继续输出旧坐标”的情况。",
        ]),
        ("P3：AprilTag 工位定位（Day 10-11）", [
            "先确认 Tag 字典、物理尺寸、ID 与工位名称；再验证距离、角度和多 Tag 场景。",
            "Tag 负责确认工位，颜色识别负责确认物料；两者结果要分别保留，不互相替代。",
            "验收门槛：未知 ID、Tag 丢失和多个 Tag 时，运行时不产生错误工位映射。",
        ]),
        ("P4-P5：接口与交付（Day 12-14）", [
            "与控制组冻结坐标单位、原点、正方向、串口参数、视觉结果字段、ACK/DONE 时序和错误码。",
            "先验证单条指令和单个物料流程，最后才运行完整任务。",
            "树莓派阶段只验证已到位设备；未到位硬件必须在报告中明确记录，不能假设已通过。",
        ]),
    ]
    for title, items in phases:
        heading(doc, title, level=2)
        for item in items:
            bullet(doc, item)

    heading(doc, "四、新人逐日操作卡")
    labeled(doc, "使用方法", "每天只完成当天这一张卡。先运行命令，再阅读指定文件，再做小实验。任何一天没有达到“通过标准”，不要跳到下一天。所有命令都在项目根目录的 cmd 终端执行。")
    add_day_guide(
        doc, 1, "基线", "确认环境和项目没有问题",
        "打开 Project001-opencv.gcs 文件夹；VSCode 左下角解释器应为 ..\\.venv\\Scripts\\python.exe。",
        [
            "在 VSCode 中打开终端；确认命令行最后一段是 Project001-opencv.gcs。",
            "先运行启动前检查；它只检查 Python 版本、依赖和配置，不会改动项目。",
            "运行全部单元测试；看到每一项为 ok 后再继续。",
            "运行一次模拟任务；只关心最后 state 是否为 complete。",
            "将当天终端输出的最后部分保存到 docs/progress/第1天.md；不会写 Markdown 时，先复制命令和 OK 结果即可。",
        ],
        "preflight 的 passed 为 true，全部测试显示 OK，模拟任务 state 为 complete。",
        "出现找不到 python.exe：检查当前目录是否为项目根目录，并使用相对路径 ..\\.venv\\Scripts\\python.exe；不要在 cmd 中输入 PowerShell 的 &。",
        "第1天.md，包含日期、三条命令和结果。",
        [
            "..\\.venv\\Scripts\\python.exe -m tools.启动检查 --json",
            "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v",
            "..\\.venv\\Scripts\\python.exe -m app.视觉应用 --mode simulation",
        ],
    )
    add_day_guide(
        doc, 2, "P0", "让电脑摄像头稳定提供画面",
        "准备一个 USB 摄像头或笔记本自带摄像头；关闭占用摄像头的微信、会议软件和浏览器网页。没有相机时，完成代码阅读和样本清单，不宣称已通过真实相机验证。",
        [
            "先阅读 app/虚拟设备.py 中 VirtualCamera.read()，理解真实相机以后也必须提供“成功标志 + 图像帧”。",
            "将红、绿、蓝三个物料放在摄像头前，背景尽量单一，光线保持稳定。",
            "运行颜色识别预览命令；窗口出现后依次展示三种物料，观察是否出现检测框、颜色名称和坐标。",
            "保持窗口运行 10 分钟；每隔 1 分钟移动一次物料，观察是否卡死、黑屏或明显掉帧。",
            "按 q 退出窗口。记录摄像头型号、分辨率、安装高度、是否自动曝光，以及首次看到的问题。",
        ],
        "能连续读帧 10 分钟；红、绿、蓝至少各出现一次检测结果；程序退出后不报错。",
        "摄像头打不开：先拔掉并重新插入，关闭其他软件；如果 source 0 失败，按 1、2 依次尝试其他摄像头编号；画面全黑时检查系统相机权限。",
        "day02.md 和一张包含物料的原始截图或短视频路径；不要只写“能用”。",
        [
            "..\\.venv\\Scripts\\python.exe -m vision.颜色追踪 --source 0 --config config\\颜色追踪配置.json --colors red green blue",
        ],
    )
    add_day_guide(
        doc, 3, "P0", "建立真实样本和场景清单",
        "完成 Day 2 的读帧；准备红、绿、蓝物料、白色与深色背景、手机补光灯或不同室内光源。",
        [
            "在 data 下创建你自己的采集目录，例如 data\\field_samples\\day03；不修改已有 shapes_dataset。",
            "为每种颜色拍摄至少 5 种情况：正面、偏转约 30 度、远一点、近一点、部分遮挡。",
            "再拍摄 4 类故障画面：强反光、阴影、没有物料、两个同色物料。",
            "给每张图片起能看懂的名字，例如 red_front_01.jpg、blue_shadow_01.jpg；名字必须体现颜色和场景。",
            "建立一个简单表格或 Markdown 列表，记录图片文件名、物料颜色、距离、光照、是否应识别。",
        ],
        "至少有 3 种颜色和 4 类失败场景；每张样本知道“理论上应该识别还是应该拒绝”。",
        "不要立刻训练 YOLO；本阶段样本用于检验现有 HSV 方案的边界。样本少于 30 张时，只能用于调试，不能据此评价泛化能力。",
        "样本清单、图片目录路径、至少 3 个你预计最难的场景。",
        [],
    )
    add_day_guide(
        doc, 4, "P0", "在真实画面上建立颜色识别基线",
        "已有 Day 3 样本；打开 vision/颜色追踪.py 和 config/颜色追踪配置.json。今天先观察，不改参数。",
        [
            "阅读配置里的 red、green、blue 三组 HSV ranges，知道每个范围对应一种颜色。",
            "阅读 tracking 中 min_area、confirm_frames、max_lost_frames 的注释或使用位置。",
            "逐个展示 Day 3 的样本，记录“识别正确、漏检、误检、颜色错分、坐标跳变”五类结果。",
            "对每次结果记录 valid 是否为 true；valid 为 false 表示系统尚不允许将这个结果交给抓取动作。",
            "挑选每类问题各一个代表样本，写下你认为的原因，例如反光、目标太小、背景太接近或颜色阈值不合适。",
        ],
        "得到第一份基线统计，不要求准确率很高，但每个失败都有截图和原因猜测。",
        "程序完全没有检测框：先确认摄像头画面里确实有物料，再确认物料颜色与当前配置相符；不要同时修改 HSV、面积和确认帧。",
        "day04.md，包含样本数量、正确数、漏检数、误检数和 3 个代表问题。",
        [
            "..\\.venv\\Scripts\\python.exe -m unittest tests.test_color_tracking -v",
        ],
    )
    add_day_guide(
        doc, 5, "P0", "通过单变量实验得到第一版参数",
        "Day 4 已有基线记录；复制 config/颜色追踪配置.json 作为备份，例如保存为 logs\\day05_color_config_backup.json。",
        [
            "第一次实验只改 min_area：提高它会过滤小噪声，也可能漏掉远处物料。每次修改后都用同一组样本测试。",
            "恢复备份后，第二次只改 confirm_frames：提高它会减少闪烁，但会让确认更慢。",
            "恢复备份后，第三次只改 max_lost_frames：提高它会允许短暂遮挡，但可能让旧目标保留更久。",
            "每次实验只保留一份报告，写清修改前值、修改后值、样本数、正确数、漏检数和误检数。",
            "选择整体退化最少的一组参数；运行完整测试确认没有破坏原有逻辑。",
        ],
        "能说清三个参数各影响什么，并能用报告解释为何选择当前值。",
        "一次改了多个参数：立即恢复备份，重新做实验；没有相同样本对比的调参不算实验。",
        "最终配置、三份实验记录、day05.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m tools.仿真验证 --output logs\\day05_visual.json",
            "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q",
        ],
    )
    add_day_guide(
        doc, 6, "P1", "采集棋盘格并完成相机内参标定",
        "准备打印清晰且平整的棋盘格；确认内角点数量，例如 9 x 6 指的是内角点，不是方格数；相机分辨率和安装位置必须固定。",
        [
            "在 data\\calibration 下保存棋盘格图片，至少拍 15 张；棋盘格要覆盖画面中心、四角和不同倾斜角度。",
            "删除模糊、过曝、只占极小区域的图片；不要为了凑数量保留坏图。",
            "运行标定命令，board-width、board-height 要与实际内角点一致，square-size 填真实单个格子的边长（毫米）。",
            "检查输出的 config\\camera_calibration.json 是否生成；不要手工修改内部参数。",
            "保留原始棋盘格图片和标定命令，后续相机重新安装后需要重做。",
        ],
        "标定文件成功生成并可被测试读取；使用相同分辨率时不报不匹配错误。",
        "找不到棋盘格角点：优先检查内角点数量、照片是否模糊、棋盘格是否反光和是否太小；不要先改算法。",
        "棋盘格图片目录、camera_calibration.json、day06.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m vision.相机标定 --images data\\calibration\\*.jpg --board-width 9 --board-height 6 --square-size 25 --output config\\camera_calibration.json",
            "..\\.venv\\Scripts\\python.exe -m unittest tests.test_camera_calibration -v",
        ],
    )
    add_day_guide(
        doc, 7, "P1", "把像素坐标转换为场地毫米坐标",
        "Day 6 标定成功；在正式工位放置一个有已知尺寸的平面，例如标出四个角的矩形纸板或固定标定板。",
        [
            "明确场地坐标原点放在哪里，x 轴和 y 轴各指向哪里；在 day07.md 画一张简单示意图。",
            "测量四个已知点的真实毫米坐标；不要估计。",
            "在 data\\calibration\\plane_points.json 填写图像像素点和对应世界坐标点。",
            "运行转换测试；再取四角和中心点作为验证点，记录预测 world_mm 与尺子实测值的差。",
            "如果误差过大，先检查相机是否移动、安装是否垂直、点顺序是否一致，再考虑重新标定。",
        ],
        "可以输出 world_mm，并有至少 5 个验证点的误差表。",
        "四个点的顺序错乱会导致坐标翻转或严重变形；必须按同一方向填写，例如左上、右上、右下、左下。",
        "plane_points.json、误差表、day07.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m unittest tests.test_camera_calibration -v",
        ],
    )
    add_day_guide(
        doc, 8, "P2", "验证稳定目标才允许抓取",
        "已经能输出 world_mm；打开 vision/精定位.py，重点看确认帧、跳变拒绝和丢失处理。",
        [
            "将物料稳定放置，观察连续多帧识别后是否从未确认变为 stable 或 valid。",
            "快速移动物料，故意制造坐标跳变；确认模块不会立即把跳变坐标交给抓取。",
            "把物料移出画面；确认目标丢失后结果不再使用旧 world_mm。",
            "记录稳定所需的帧数和大致时间；这会影响最终抓取节奏。",
            "运行专项测试，确保你对真实画面的观察与已有逻辑一致。",
        ],
        "稳定目标可通过，跳变和丢失目标被拒绝；没有“旧坐标继续抓取”。",
        "目标一直不稳定：先检查摄像头固定性、光照和 Day 5 参数；不要先无限提高确认帧数。",
        "day08.md，包含稳定、跳变、丢失三种场景的结果。",
        [
            "..\\.venv\\Scripts\\python.exe -m unittest tests.test_stage2_workflow.Stage2Tests.test_fine_localization_rejects_jump_and_confirms_stable_mean -v",
        ],
    )
    add_day_guide(
        doc, 9, "P2", "建立视觉失败案例库",
        "完成 P0-P1；准备 Day 3 的故障样本，并补充当天新拍的遮挡、反光和多目标画面。",
        [
            "对每个失败样本运行识别，记录最终是漏检、误检、错分、跳变还是安全拒绝。",
            "同色多目标时，记录系统输出了几个 target，以及你期望控制端如何选择。",
            "遮挡和无目标时，检查 valid 是否为 false，而不是只看窗口还有没有检测框。",
            "把每个问题写成“输入条件 -> 实际结果 -> 期望结果 -> 下一步处理”的格式。",
            "选择最影响比赛的一项问题作为下一轮改进目标；不要一天修全部问题。",
        ],
        "至少形成 10 个失败案例；每个案例都可复现并有处理结论。",
        "不要把“识别不到”一律当 Bug；在高风险场景拒绝执行往往是正确的安全行为。",
        "failures.md、样本路径、day09.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m tools.离线评测 --output logs\\day09_fault_review.json",
        ],
    )
    add_day_guide(
        doc, 10, "P3", "完成 AprilTag 基础识别与工位映射",
        "准备打印或购买的 AprilTag；确认字典、每个 Tag 的 ID、Tag 实际边长和每个工位名称。",
        [
            "打开 config\\站点标签.json，将 Tag ID 映射到团队约定的工位名称；不要随意改已有字段名。",
            "先在固定距离和正面角度下测试一个 Tag，确认能识别 ID。",
            "再测试未知 ID、没有 Tag、两个 Tag；记录输出而不是只测试成功场景。",
            "在 day10.md 写清 Tag 字典、物理尺寸、识别距离和摄像头安装位置。",
            "确认“识别物料”和“识别工位”是两件事：颜色结果不能代替 Tag，Tag 也不能代替物料识别。",
        ],
        "正常 Tag 能映射工位；未知、丢失、多 Tag 时没有错误工位结果。",
        "完全识别不到 Tag：确认打印清晰、没有反光、字典一致、Tag 尺寸配置正确；先不要改状态机。",
        "站点标签.json、Tag 测试记录、day10.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m unittest tests.test_stage2_workflow.Stage2Tests.test_apriltag_station_detection_works_without_camera_calibration -v",
        ],
    )
    add_day_guide(
        doc, 11, "P3", "形成固定工位视觉闭环",
        "Day 10 完成；在一个固定工位同时放置 Tag 和一个彩色物料。",
        [
            "先确认 Tag 稳定识别，再确认物料稳定识别；两者分别记录。",
            "检查运行时读取到的 visual_result 和 stations；它们必须来自同一轮画面处理。",
            "为这个工位定义一条最小输出：工位名、物料颜色、pixel、world_mm、valid、confidence。",
            "遮住 Tag 或移走物料，检查输出是否明确表示缺失，而不是沿用上一次结果。",
            "连续运行 20 次，将正确次数和失败原因写入记录。",
        ],
        "能稳定给出“在哪个工位看到了什么物料，以及是否可信”。",
        "只要 Tag 或物料其中一个缺失，就不能把结果称为完整工位闭环。",
        "固定工位 20 次记录、字段样例、day11.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m app.视觉应用 --mode simulation --log logs\\day11_application.jsonl",
        ],
    )
    add_day_guide(
        doc, 12, "P4", "冻结给控制组的视觉接口",
        "带上 Day 7、Day 8、Day 10、Day 11 的记录；与控制组约一个短会议，先冻结字段再开始串口联调。",
        [
            "写清每个字段：颜色名称、物料 ID、pixel、world_mm、yaw、valid、confidence、时间戳和失败原因。",
            "明确坐标单位为毫米还是米，原点在哪里，x/y 正方向是什么；用一张图确认。",
            "明确 valid 为 false 时控制组必须做什么，例如停止、重拍、后退或请求人工处理。",
            "明确视觉模块何时发新结果、控制端何时确认收到、超时后谁负责安全停止。",
            "使用现有协议测试了解 ACK、DONE、FAIL 的含义；今天重点是接口约定，不要求你独自写 STM32 代码。",
        ],
        "形成一页可交给控制组的接口清单，双方对字段和失败处理没有歧义。",
        "不要只说“给坐标”；必须说清坐标系、单位、可信条件和失效时的动作。",
        "vision_control_interface.md、会议结论、day12.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m unittest tests.test_stage2_workflow.Stage2Tests.test_protocol_action_bridge_completes_over_memory_serial -v",
        ],
    )
    add_day_guide(
        doc, 13, "P5", "在树莓派运行软件主链路",
        "准备树莓派、电源、网络和存储卡；先复制项目代码，不要直接在唯一一份电脑代码上冒险改环境。",
        [
            "在树莓派上安装 Python、venv 和 pip，创建项目自己的 .venv。",
            "安装 requirements.txt 后先运行 preflight；缺依赖先解决，不直接运行主程序。",
            "运行模拟任务，检查日志是否持续写入。",
            "观察 CPU、内存和温度；记录运行开始和结束状态。",
            "暂时没有相机时，只宣称模拟模式通过；相机读帧要单独验收。",
        ],
        "树莓派 preflight 通过，模拟任务 complete，日志生成且资源没有异常持续增长。",
        "pip 安装失败：确认网络、Python 版本和虚拟环境是否已激活；不要用 sudo pip 安装到系统环境。",
        "树莓派安装步骤、依赖版本、运行日志、day13.md。",
        [
            "python3 -m venv .venv",
            "source .venv/bin/activate",
            "python -m pip install -r requirements.txt",
            "python -m tools.启动检查 --json",
            "python -m app.视觉应用 --mode simulation --log logs/day13_raspberry_pi.jsonl",
        ],
    )
    add_day_guide(
        doc, 14, "P5", "最终回归并交付联调版本",
        "收齐 Day 1-13 的配置、样本、日志和失败案例；不要在最后一天引入新算法或大改参数。",
        [
            "运行全部单元测试、离线评测和 Python 编译检查。",
            "在树莓派上重复运行模拟任务；有真实相机时，只做单独读帧和单物料识别验收。",
            "整理当前已通过、未通过、未测试三类项目，未到位硬件必须写明。",
            "导出控制组需要的配置：颜色参数、Tag 映射、相机标定、坐标系和接口清单。",
            "创建一个稳定版本标签或至少保留可回退的 Git 提交；比赛前不要只保留一个可修改版本。",
        ],
        "测试、评测、配置和接口清单可复查；团队知道系统能做什么、不能做什么以及下一步由谁处理。",
        "最后一天出现问题：优先回退到最近一次测试通过的配置，不在现场临时堆新功能。",
        "final_report.md、离线评测 JSON、联调清单、未完成硬件清单、day14.md。",
        [
            "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v",
            "..\\.venv\\Scripts\\python.exe -m tools.离线评测 --output logs\\final_evaluation.json",
            "..\\.venv\\Scripts\\python.exe -m compileall -q app mission protocol vision tools tests",
        ],
    )

    heading(doc, "五、每日固定工作流")
    labeled(doc, "时间分配", "10 分钟明确当天验收目标；70 分钟开发或实验；30 分钟测试和记录；10 分钟填写进度与提交。")
    labeled(doc, "每日必做", "运行相关测试，保留日志和样本路径，写清成功率/失败案例/参数变化，未验收通过则继续当前任务。")
    code(doc, "..\\.venv\\Scripts\\python.exe -m tools.启动检查 --json")
    code(doc, "..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q")
    labeled(doc, "Day 2 启动命令", "先确认电脑摄像头可读帧；没有摄像头或权限时，先完成样本清单和接口阅读，不伪造真实验收结果。")
    code(doc, "..\\.venv\\Scripts\\python.exe -m vision.颜色追踪 --source 0 --config config\\颜色追踪配置.json --colors red green blue")

    heading(doc, "六、每个阶段的交付物")
    add_table(
        doc,
        ["阶段", "必须交付", "不能用什么代替"],
        [
            ("P0", "样本清单、原始图片或视频路径、参数文件、识别日志、成功/失败统计。", "不能只用仿真测试代替真实画面验证。"),
            ("P1", "相机标定文件、转换配置、坐标误差记录。", "不能用像素坐标声称可抓取。"),
            ("P2", "跳变/丢失/遮挡测试日志和拒绝执行证据。", "不能只展示正常识别视频。"),
            ("P3", "Tag 配置、工位映射、未知与丢失场景结果。", "不能只验证一个固定 Tag。"),
            ("P4-P5", "接口清单、树莓派日志、回归报告、未完成问题清单。", "不能把模拟串口当作真实联调完成。"),
        ],
        [900, 5000, 3460],
    )

    heading(doc, "七、P6：YOLO 的启动条件")
    bullet(doc, "只有在 P0 的真实样本证明 HSV/轮廓方案无法满足物料区分、遮挡或背景变化要求时，才启动 YOLO。")
    bullet(doc, "启动前必须具备：确定的类别定义、足够真实样本、标注方案、树莓派或加速硬件的推理预算，以及明确的对比基线。")
    bullet(doc, "YOLO 的目标是补齐已确认的失败案例，不是替代标定、稳定性判断和安全拒绝策略。")

    heading(doc, "八、9 月控制组联调前冻结项")
    for item in [
        "坐标单位、坐标原点、正方向、工位坐标与允许误差。",
        "视觉结果字段：物料 ID、颜色、pixel、world_mm、valid、confidence、时间戳与失败原因。",
        "AprilTag ID、工位名称、物料编号、颜色名称和环码。",
        "串口参数、协议版本、命令字、ACK/DONE/FAIL 时序与错误码。",
        "目标丢失、Tag 丢失、通信断开和安全停止后的恢复方式。",
    ]:
        bullet(doc, item)

    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build()
