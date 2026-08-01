# YOLO 10 天零基础实战教程（有 Python 基础版）

> **适用对象**：具备一定 Python 基础，但尚未系统学习目标检测与 YOLO\
> **阶段目标**：能训练自己的模型 → 能独立修改数据集 →
> 能使用摄像头实时检测 → 能输出结构化检测结果\
> **项目方向**：工创赛智能物流 / 机器人视觉模块 V1.0\
> **建议投入**：10 天，每天 3--4 小时

------------------------------------------------------------------------

## 0. 学习原则与最终成果

本阶段不追求"学完所有 YOLO 版本"，也不进行复杂模型魔改。主线围绕
Ultralytics YOLO 完成一个可运行的工程闭环：

``` text
环境搭建
  ↓
预训练模型推理
  ↓
解析检测结果
  ↓
理解 YOLO 数据集
  ↓
制作自定义数据集
  ↓
训练自己的模型
  ↓
评估与诊断
  ↓
迭代数据集
  ↓
OpenCV 摄像头实时检测
  ↓
输出类别、置信度、BBox、中心点
```

### 10 天总览

  天数     学习主题                核心产出
  -------- ----------------------- --------------------------------
  Day 1    YOLO 入门与环境搭建     跑通图片、视频、摄像头推理
  Day 2    Python API 与结果解析   获取类别、置信度、BBox、中心点
  Day 3    YOLO 数据集原理         理解标签格式并完成标签可视化
  Day 4    自定义数据集制作        完成 Dataset V1
  Day 5    第一次自定义训练        获得自己的 `best.pt`
  Day 6    模型评估与错误诊断      会看 P、R、mAP 和失败案例
  Day 7    数据集迭代              Dataset V1 → V2，Model V1 → V2
  Day 8    OpenCV 实时检测         完成摄像头实时检测程序
  Day 9    机器人视觉接口          输出结构化目标信息并筛选目标
  Day 10   项目工程化              完成"工创赛视觉模块 V1.0"

------------------------------------------------------------------------

# Day 1：YOLO 入门、环境搭建与第一次推理

## 1. 知识点

理解目标检测的基本任务：

-   **Class**：目标类别
-   **Confidence**：模型对预测结果的置信度
-   **Bounding Box（BBox）**：目标所在矩形区域
-   **Inference / Predict**：使用训练好的模型进行推理
-   **Model Weight**：模型权重文件，如 `*.pt`

目标检测的基本输入输出：

``` text
Image / Video / Camera
          ↓
         YOLO
          ↓
Class + Confidence + Bounding Box
```

## 2. 推荐资料

### 官方 / GitHub

-   Ultralytics YOLO
    GitHub：<https://github.com/ultralytics/ultralytics>
-   Ultralytics 官方文档：<https://docs.ultralytics.com/>
-   Ultralytics Quickstart：<https://docs.ultralytics.com/quickstart/>

### 哔哩哔哩

由于视频标题和版本更新较快，优先按关键词筛选近期高质量教程：

-   搜索：`Ultralytics YOLO 入门 教程`
-   搜索：`YOLO11 目标检测 自定义数据集`
-   搜索：`YOLOv8 从零开始 目标检测`

建议优先选择： 1. 有完整环境配置； 2. 同时讲 CLI 与 Python API； 3.
包含自定义数据集训练； 4. 评论区没有大量"版本已失效"反馈的视频。

> **学习策略**：视频只负责建立直觉，实际命令和 API 以官方文档为准。

## 3. 操作步骤

### 3.1 创建 Conda 环境

``` bash
conda create -n yolo python=3.10 -y
conda activate yolo
```

### 3.2 安装 Ultralytics

``` bash
pip install ultralytics
```

检查环境：

``` bash
yolo checks
```

### 3.3 第一次图片推理

准备 `test.jpg`：

``` python
from ultralytics import YOLO

model = YOLO("yolo11n.pt")

model.predict(
    source="test.jpg",
    conf=0.5,
    save=True
)
```

### 3.4 视频与摄像头

``` python
# 视频
model.predict(source="video.mp4", conf=0.5, save=True)

# 默认摄像头
model.predict(source=0, conf=0.5, show=True)
```

> 如果当前安装版本的官方推荐模型名称发生变化，以官方文档为准。学习重点是掌握统一
> API，而不是死记某一个模型版本。

## 4. 常见报错

### `ModuleNotFoundError: No module named 'ultralytics'`

``` bash
conda activate yolo
pip install ultralytics
```

### 找不到图片

``` python
from pathlib import Path

path = Path("test.jpg")
print(path.resolve())
print(path.exists())
```

### 摄像头打不开

尝试：

``` python
source=1
```

并关闭可能占用摄像头的软件。

## 5. 当日作业

-   [ ] 完成 1 张图片推理
-   [ ] 完成 1 个视频推理
-   [ ] 打开摄像头实时检测
-   [ ] 对比 `conf=0.25 / 0.50 / 0.75`
-   [ ] 记录不同阈值下的检测变化

## 6. 验收标准

能够独立解释：

1.  YOLO 的输入和输出是什么？
2.  `conf` 参数有什么作用？
3.  图片、视频和摄像头的 `source` 有什么区别？

------------------------------------------------------------------------

# Day 2：Python API 与检测结果解析

## 1. 知识点

重点理解：

``` text
results
└── results[0]
    ├── boxes
    │   ├── xyxy
    │   ├── cls
    │   └── conf
    └── names
```

你需要从"看到检测框"升级到"程序能够读取检测结果"。

## 2. 推荐资料

### 官方

-   Predict Mode：<https://docs.ultralytics.com/modes/predict/>
-   Ultralytics GitHub `examples`
    目录：<https://github.com/ultralytics/ultralytics/tree/main/examples>

### 哔哩哔哩搜索关键词

-   `Ultralytics YOLO Python API results boxes`
-   `YOLO 检测结果 置信度 坐标 获取`
-   `YOLO OpenCV 检测框 坐标`

## 3. 完整代码

``` python
from ultralytics import YOLO

model = YOLO("yolo11n.pt")
results = model("test.jpg")

result = results[0]

if len(result.boxes) == 0:
    print("未检测到目标")

for box in result.boxes:
    class_id = int(box.cls[0])
    confidence = float(box.conf[0])
    x1, y1, x2, y2 = box.xyxy[0].tolist()

    class_name = result.names[class_id]

    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    print(
        f"类别: {class_name}, "
        f"置信度: {confidence:.2f}, "
        f"BBox: ({x1:.0f}, {y1:.0f}, {x2:.0f}, {y2:.0f}), "
        f"中心点: ({center_x:.0f}, {center_y:.0f})"
    )
```

## 4. 常见报错

### 没有检测框却直接访问数据

先判断：

``` python
if len(result.boxes) == 0:
    print("未检测到目标")
```

### Tensor 不能直接用于某些 Python 操作

必要时使用：

``` python
value = float(tensor_item)
data = tensor.tolist()
```

## 5. 当日作业

输出如下格式：

``` text
检测目标数量：3

目标 1
类别：person
置信度：0.93
BBox：(100, 80, 300, 500)
中心点：(200, 290)
```

## 6. 验收标准

不看教程，能够获取：

-   [ ] `class_id`
-   [ ] `class_name`
-   [ ] `confidence`
-   [ ] `x1, y1, x2, y2`
-   [ ] `center_x, center_y`

------------------------------------------------------------------------

# Day 3：彻底理解 YOLO 数据集格式

## 1. 知识点

标准目录：

``` text
dataset/
├── images/
│   ├── train/
│   └── val/
├── labels/
│   ├── train/
│   └── val/
└── data.yaml
```

图片和标签必须对应：

``` text
images/train/001.jpg
labels/train/001.txt
```

YOLO 检测标签格式：

``` text
class_id x_center y_center width height
```

例如：

``` text
0 0.500 0.400 0.200 0.300
```

坐标通常归一化到 `[0, 1]`。

## 2. 推荐资料

### 官方

-   Dataset Guide：<https://docs.ultralytics.com/datasets/>
-   Object Detection Dataset
    Guide：<https://docs.ultralytics.com/datasets/detect/>

### 开源标注工具

-   CVAT：<https://github.com/cvat-ai/cvat>
-   Label Studio：<https://github.com/HumanSignal/label-studio>
-   LabelImg：<https://github.com/HumanSignal/labelImg>

### 哔哩哔哩搜索关键词

-   `YOLO 数据集格式 详解`
-   `YOLO 标签格式 xywh 归一化`
-   `YOLO 自定义数据集 标注 教程`

## 3. 坐标转换

``` python
x_center = ((x1 + x2) / 2) / image_width
y_center = ((y1 + y2) / 2) / image_height
width = (x2 - x1) / image_width
height = (y2 - y1) / image_height
```

## 4. `data.yaml` 示例

``` yaml
path: D:/Projects/yolo_project/dataset

train: images/train
val: images/val

names:
  0: red_cube
  1: blue_cube
  2: green_cube
```

## 5. 标签可视化完整代码

``` python
import cv2

image_path = "dataset/images/train/001.jpg"
label_path = "dataset/labels/train/001.txt"

image = cv2.imread(image_path)

if image is None:
    raise FileNotFoundError(f"无法读取图片: {image_path}")

height, width = image.shape[:2]

with open(label_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for line in lines:
    class_id, xc, yc, w, h = map(float, line.split())

    x1 = int((xc - w / 2) * width)
    y1 = int((yc - h / 2) * height)
    x2 = int((xc + w / 2) * width)
    y2 = int((yc + h / 2) * height)

    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)

cv2.imshow("Label Check", image)
cv2.waitKey(0)
cv2.destroyAllWindows()
```

## 6. 常见错误

-   类别编号与 `data.yaml` 不一致
-   图片与 TXT 文件名不一致
-   漏标
-   错标
-   框包含大量无关背景
-   框没有完整覆盖目标
-   坐标没有归一化

## 7. 当日作业

-   [ ] 手工理解至少 5 个 TXT 标签
-   [ ] 可视化检查至少 20 张标注图片
-   [ ] 自己写一个 3 类的 `data.yaml`

## 8. 验收标准

面对一个新类别，能够回答：

1.  图片放哪里？
2.  标签放哪里？
3.  TXT 文件写什么？
4.  `data.yaml` 怎么修改？

------------------------------------------------------------------------

# Day 4：制作自己的工创赛数据集 Dataset V1

## 1. 知识点

第一版建议：

-   类别：3--5 类
-   快速验证集：100--200 张
-   正式 V1：300--800 张
-   划分建议：Train 80%，Val 20%

数据需要覆盖：

-   不同距离
-   不同角度
-   不同光照
-   不同背景
-   不同目标尺寸
-   部分遮挡
-   多目标场景

## 2. 推荐资料

### 工具

-   CVAT：<https://github.com/cvat-ai/cvat>
-   Label Studio：<https://github.com/HumanSignal/label-studio>

### 哔哩哔哩搜索关键词

-   `CVAT YOLO 标注 教程`
-   `LabelImg YOLO 数据标注`
-   `YOLO 自定义数据集 制作 训练`
-   `目标检测 数据集 采集 标注 规范`

## 3. 操作步骤

1.  明确类别表，不要训练中途频繁改名。
2.  采集真实比赛场景数据。
3.  完成标注。
4.  划分 Train / Val。
5.  编写 `data.yaml`。
6.  随机抽查至少 30 张。
7.  使用 Day 3 的可视化程序检查标签。

> 不要把同一段视频的连续相邻帧大量随机拆分到 Train 和
> Val，否则容易造成数据泄漏。

## 4. 常见问题

### 数据很多但泛化很差

原因可能是大量连续帧高度相似。应增加场景多样性，而不是单纯增加数量。

### 类别样本严重不均衡

检查每个类别的图片数量和出现次数，避免某一类别远多于其他类别。

## 5. 当日作业

完成：

``` text
dataset_v1/
├── images/
│   ├── train/
│   └── val/
├── labels/
│   ├── train/
│   └── val/
└── data.yaml
```

## 6. 验收标准

随机抽查 30 张：

-   [ ] 无明显漏标
-   [ ] 无明显错标
-   [ ] 类别编号正确
-   [ ] 框的位置合理
-   [ ] Train / Val 划分合理

------------------------------------------------------------------------

# Day 5：第一次训练自己的模型

## 1. 知识点

理解四个核心参数：

-   `epochs`：训练轮数
-   `batch`：每批样本数量
-   `imgsz`：输入图像尺寸
-   `device`：训练设备

第一轮训练的目标是**跑通完整闭环**，而不是立即追求最高精度。

## 2. 推荐资料

### 官方

-   Train Mode：<https://docs.ultralytics.com/modes/train/>
-   Ultralytics GitHub：<https://github.com/ultralytics/ultralytics>

### 哔哩哔哩搜索关键词

-   `Ultralytics YOLO 自定义数据集 训练`
-   `YOLO11 train data yaml 教程`
-   `YOLOv8 自定义数据集 训练 参数`

## 3. 完整训练代码

``` python
from ultralytics import YOLO

model = YOLO("yolo11n.pt")

model.train(
    data="dataset_v1/data.yaml",
    epochs=50,
    imgsz=640,
    batch=16,
    device=0,
    project="runs",
    name="experiment_v1"
)
```

没有 NVIDIA GPU：

``` python
device="cpu"
```

训练后重点查看：

``` text
runs/
└── experiment_v1/
    └── weights/
        ├── best.pt
        └── last.pt
```

## 4. 常见报错

### CUDA Out of Memory

降低 Batch：

``` python
batch=8
```

或：

``` python
batch=4
```

### 数据集路径错误

Windows 推荐：

``` yaml
path: D:/Projects/yolo_project/dataset_v1
```

### CUDA / PyTorch 环境问题

先确认：

``` python
import torch

print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
```

## 5. 当日作业

-   [ ] 完成至少一次完整训练
-   [ ] 找到 `best.pt`
-   [ ] 用 `best.pt` 对未参与训练的图片推理
-   [ ] 保存训练参数和实验结果

## 6. 验收标准

能够解释：

1.  `best.pt` 和 `last.pt` 的区别；
2.  如何重新加载自己的模型；
3.  Batch 太大导致显存不足时如何处理。

------------------------------------------------------------------------

# Day 6：模型评估与错误诊断

## 1. 知识点

重点理解：

-   **Precision**：预测为目标的结果中，有多少是真的
-   **Recall**：真实目标中，有多少被成功找到
-   **mAP50**：IoU=0.5 下的平均检测精度
-   **mAP50-95**：更严格的综合检测指标

## 2. 推荐资料

### 官方

-   Val Mode：<https://docs.ultralytics.com/modes/val/>
-   Performance
    Metrics：<https://docs.ultralytics.com/guides/yolo-performance-metrics/>

### 哔哩哔哩搜索关键词

-   `目标检测 Precision Recall mAP 讲解`
-   `YOLO mAP50 mAP50-95 混淆矩阵`
-   `目标检测 模型评估 指标`

## 3. 验证代码

``` python
from ultralytics import YOLO

model = YOLO("runs/experiment_v1/weights/best.pt")

metrics = model.val()

print(metrics)
```

## 4. 建立错误案例库

``` text
error_cases/
├── missed/          # 漏检
├── false_positive/  # 误检
├── wrong_class/     # 分类错误
└── difficult/       # 困难场景
```

诊断顺序：

``` text
检查数据
  ↓
检查标注
  ↓
分析失败场景
  ↓
补充困难样本
  ↓
重新训练
  ↓
最后才考虑更换或增大模型
```

## 5. 常见误区

-   只看 mAP，不测试真实比赛环境
-   模型效果差就立即换大模型
-   验证集与训练集过于相似
-   不保存失败案例
-   只测试"好看的成功案例"

## 6. 当日作业

至少收集并分析 10 个失败案例，为每个案例标注原因。

## 7. 验收标准

能够初步判断问题属于：

-   [ ] 数据问题
-   [ ] 标注问题
-   [ ] 阈值问题
-   [ ] 场景分布问题
-   [ ] 模型能力问题

------------------------------------------------------------------------

# Day 7：真正掌握"修改数据集"

## 1. 知识点

完成：

``` text
Dataset V1 → Dataset V2
Model V1   → Model V2
```

示例：

``` text
V1:
0 red_cube
1 blue_cube
2 green_cube

V2:
0 red_cube
1 blue_cube
2 green_cube
3 yellow_cube
```

## 2. 推荐资料

### 官方

-   Dataset Guide：<https://docs.ultralytics.com/datasets/>
-   Train Mode：<https://docs.ultralytics.com/modes/train/>

### 哔哩哔哩搜索关键词

-   `YOLO 增加类别 重新训练`
-   `目标检测 困难样本 数据集优化`
-   `YOLO 数据增强 自定义数据集`

## 3. 操作步骤

1.  分析 Day 6 的失败案例。
2.  确定新增类别或困难场景。
3.  针对性采集数据。
4.  标注并检查。
5.  更新 `data.yaml`。
6.  训练 Model V2。
7.  使用相同测试集比较 V1 与 V2。

## 4. 实验记录表

  模型   数据集       主要变化              Precision   Recall   mAP50 真实场景表现
  ------ ------------ ------------------- ----------- -------- ------- --------------
  V1     Dataset V1   基线                                             
  V2     Dataset V2   增加困难样本/类别                                

## 5. 常见错误

-   修改类别顺序后旧标签没有同步更新
-   V1 和 V2 使用不同测试条件，无法公平比较
-   只增加简单样本，没有针对失败场景补数据

## 6. 当日作业

完成 Dataset V2 与 Model V2，并形成一次实验对比记录。

## 7. 验收标准

能够独立完成：

-   [ ] 增加类别
-   [ ] 删除或重构类别
-   [ ] 补充困难样本
-   [ ] 修改 `data.yaml`
-   [ ] 重新训练
-   [ ] 对比两个模型版本

------------------------------------------------------------------------

# Day 8：OpenCV 摄像头实时检测

## 1. 知识点

理解实时视觉管线：

``` text
Camera
  ↓
OpenCV 读取 Frame
  ↓
YOLO 推理
  ↓
绘制检测结果
  ↓
OpenCV 显示
```

## 2. 推荐资料

### GitHub / 官方

-   OpenCV Python
    Tutorials：<https://docs.opencv.org/4.x/d6/d00/tutorial_py_root.html>
-   OpenCV GitHub：<https://github.com/opencv/opencv>
-   Ultralytics Predict
    Mode：<https://docs.ultralytics.com/modes/predict/>

### 哔哩哔哩搜索关键词

-   `OpenCV Python 摄像头 VideoCapture 教程`
-   `YOLO OpenCV 摄像头 实时检测`
-   `Python OpenCV 实时视频处理`

## 3. 完整代码

``` python
import cv2
from ultralytics import YOLO

MODEL_PATH = "best.pt"
CAMERA_ID = 0

model = YOLO(MODEL_PATH)
cap = cv2.VideoCapture(CAMERA_ID)

if not cap.isOpened():
    raise RuntimeError("无法打开摄像头")

while True:
    ret, frame = cap.read()

    if not ret:
        print("读取摄像头失败")
        break

    results = model.predict(
        frame,
        conf=0.5,
        verbose=False
    )

    annotated_frame = results[0].plot()

    cv2.imshow(
        "YOLO Real-Time Detection",
        annotated_frame
    )

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
```

## 4. 常见问题

### FPS 很低

可能原因：

-   CPU 推理
-   模型较大
-   输入分辨率高
-   摄像头分辨率高
-   电脑性能有限

当前阶段优化优先级：

``` text
正确性 → 稳定性 → 实时性 → 极致性能
```

## 5. 当日作业

测试并记录：

-   [ ] 正常光照
-   [ ] 弱光
-   [ ] 近距离
-   [ ] 远距离
-   [ ] 遮挡
-   [ ] 多目标

## 6. 验收标准

程序能够持续运行，摄像头画面稳定，并能检测自己的目标。

------------------------------------------------------------------------

# Day 9：把 YOLO 变成机器人视觉模块

## 1. 知识点

机器人真正需要的不是"画框"，而是结构化结果：

``` python
{
    "class_name": "red_cube",
    "confidence": 0.93,
    "bbox": [100, 80, 300, 400],
    "center_x": 200,
    "center_y": 240
}
```

## 2. 推荐资料

### 官方 / GitHub

-   Ultralytics Predict
    Mode：<https://docs.ultralytics.com/modes/predict/>
-   OpenCV GitHub：<https://github.com/opencv/opencv>

### 哔哩哔哩搜索关键词

-   `YOLO 检测结果 坐标 获取 Python`
-   `YOLO 目标中心点 OpenCV`
-   `机器人视觉 YOLO 目标定位`

## 3. 完整核心代码

``` python
from ultralytics import YOLO

model = YOLO("best.pt")


def detect_objects(frame, conf_threshold=0.5):
    results = model.predict(
        frame,
        conf=conf_threshold,
        verbose=False
    )

    detections = []

    for box in results[0].boxes:
        class_id = int(box.cls[0])
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxy[0].tolist()

        center_x = int((x1 + x2) / 2)
        center_y = int((y1 + y2) / 2)

        class_name = results[0].names[class_id]

        detections.append({
            "class_id": class_id,
            "class_name": class_name,
            "confidence": confidence,
            "bbox": [
                int(x1), int(y1),
                int(x2), int(y2)
            ],
            "center_x": center_x,
            "center_y": center_y
        })

    return detections
```

筛选指定类别并选择最高置信度目标：

``` python
def select_target(detections, target_class):
    candidates = [
        item for item in detections
        if item["class_name"] == target_class
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: item["confidence"]
    )
```

## 4. 常见问题

### 多个同类目标如何选择？

可按任务选择：

-   最高置信度
-   最大检测框
-   距离图像中心最近
-   最左 / 最右目标
-   指定 ROI 内的目标

## 5. 当日作业

实现：

``` text
摄像头
  ↓
检测所有目标
  ↓
筛选指定类别
  ↓
选择目标
  ↓
输出中心像素坐标
```

## 6. 验收标准

能够稳定输出指定目标的：

-   [ ] 类别
-   [ ] 置信度
-   [ ] BBox
-   [ ] 中心像素坐标

------------------------------------------------------------------------

# Day 10：完成"工创赛视觉模块 V1.0"

## 1. 推荐工程结构

``` text
yolo_robot_vision/
├── models/
│   └── best.pt
├── datasets/
│   ├── dataset_v1/
│   └── dataset_v2/
├── src/
│   ├── camera.py
│   ├── detector.py
│   └── main.py
├── configs/
│   └── config.yaml
├── tests/
├── 项目说明.md
└── requirements.txt
```

## 2. V1.0 功能要求

``` text
启动程序
  ↓
打开摄像头
  ↓
加载 YOLO 模型
  ↓
实时检测
  ↓
筛选指定目标
  ↓
输出：
类别
置信度
BBox
像素中心点
```

## 3. 推荐资料

### GitHub

-   Ultralytics YOLO：<https://github.com/ultralytics/ultralytics>
-   OpenCV：<https://github.com/opencv/opencv>
-   CVAT：<https://github.com/cvat-ai/cvat>

### 哔哩哔哩搜索关键词

-   `Python 项目工程化 配置文件 日志`
-   `YOLO 项目实战 自定义数据集 实时检测`
-   `机器人视觉 OpenCV YOLO 项目`

## 4. 最终 README 至少包含

``` markdown
# 项目名称

## 项目简介

## 环境要求

## 安装方法

## 数据集类别

## 训练方法

## 运行方法

## 项目结构

## 当前效果

## 已知问题

## 下一步计划
```

## 5. 最终验收任务

在一个新的终端中，从项目根目录开始：

1.  激活环境；
2.  启动程序；
3.  自动加载模型；
4.  打开摄像头；
5.  检测比赛目标；
6.  输出目标信息；
7.  按 `q` 正常退出。

## 6. 验收标准

如果不依赖逐行照抄教程，你能够独立完成以下流程，则第一阶段通过：

``` text
新目标
  ↓
采集数据
  ↓
标注
  ↓
修改 data.yaml
  ↓
训练
  ↓
评估
  ↓
加载 best.pt
  ↓
摄像头实时检测
  ↓
获取类别与像素位置
```

------------------------------------------------------------------------

# 附录 A：推荐学习资源索引

## A.1 必学官方资源

  ------------------------------------------------------------------------------------------------
  资源                                                         用途
  ------------------------------------------------------------ -----------------------------------
  <https://github.com/ultralytics/ultralytics>                 主线开源项目

  <https://docs.ultralytics.com/>                              官方总文档

  <https://docs.ultralytics.com/quickstart/>                   环境与快速开始

  <https://docs.ultralytics.com/modes/predict/>                推理

  <https://docs.ultralytics.com/modes/train/>                  训练

  <https://docs.ultralytics.com/modes/val/>                    验证

  <https://docs.ultralytics.com/datasets/>                     数据集

  <https://docs.opencv.org/4.x/d6/d00/tutorial_py_root.html>   OpenCV Python
  ------------------------------------------------------------------------------------------------

## A.2 推荐开源项目

  项目               当前阶段用途                   学习优先级
  ------------------ ---------------------------- ------------
  Ultralytics YOLO   训练、推理、验证、后续部署          ★★★★★
  OpenCV             摄像头、图像与视频处理              ★★★★★
  CVAT               数据标注                            ★★★★☆
  Label Studio       通用数据标注                        ★★★☆☆
  YOLOv5             后续阅读经典工程源码                ★★★☆☆

## A.3 B站学习关键词路线

### Day 1--2

``` text
Ultralytics YOLO 入门
YOLO11 目标检测 教程
YOLO Python API 检测结果
```

### Day 3--4

``` text
YOLO 数据集格式
YOLO 自定义数据集 标注
CVAT YOLO 标注
```

### Day 5--7

``` text
YOLO 自定义数据集 训练
目标检测 Precision Recall mAP
YOLO 数据集优化 困难样本
```

### Day 8--10

``` text
OpenCV Python 摄像头
YOLO OpenCV 实时检测
YOLO 获取目标坐标
机器人视觉 YOLO 目标定位
```

> B站教程用于辅助理解，不建议连续观看数十小时"全集"。每个章节优先找 1
> 个能解决当前问题的视频，立即回到代码实践。

------------------------------------------------------------------------

# 附录 B：10 天每日学习时间模板

建议每天 3--4 小时：

``` text
30–45 min   学习知识点 / 官方文档
45–60 min   跟做示例
90–120 min  独立实践
20–30 min   排错与记录
10 min      完成验收清单
```

------------------------------------------------------------------------

# 附录 C：本阶段不要过早学习的内容

暂时不要把主要时间投入到：

-   从 YOLOv1 开始逐代学习所有版本
-   大量阅读模型论文
-   Backbone / Neck / Head 魔改
-   自定义 Loss
-   复杂超参数搜索
-   TensorRT
-   RKNN
-   RK3576 NPU 部署

完成本教程后再进入：

``` text
阶段 2：YOLO 原理 + OpenCV + 相机标定
阶段 3：ONNX → RKNN → RK3576
阶段 4：像素坐标 → 相机坐标 → 世界坐标 → 机器人控制
```

------------------------------------------------------------------------

# 最终学习原则

> **每天必须产生一个可运行成果，而不是只"看懂了"。**

``` text
Day 1：官方模型能跑
Day 2：检测结果能读
Day 3：标签格式能懂
Day 4：数据集能做
Day 5：自己的模型能训
Day 6：失败原因能分析
Day 7：数据集能迭代
Day 8：摄像头能实时检测
Day 9：结果能结构化输出
Day 10：项目能完整运行
```
