from ultralytics import YOLO
model = YOLO("yolo11n.pt")

model.predict(
    source=r"A:\视频文件\剪映文件\77.mp4",
    conf=0.7,
    save=True
)