import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.视觉应用 import ActionLogger, ApplicationConfig, SimulationRunner
from app.虚拟设备 import VirtualCamera, VirtualScanner, VirtualSensors
from app.运行循环 import create_simulation_runtime
from app.安全监控 import SafetyMonitor
from tools.离线评测 import evaluate
from tools.启动检查 import run_preflight
from vision.精定位 import FineLocalizer, LocalizationStatus
from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine
from mission.扫码读取 import ExternalScannerParser, ScanResult, ScanType, parse_task_qr
from protocol.串口协议 import Frame, FrameParser, MessageType, ProtocolEndpoint, crc16
from protocol.传输通道 import create_memory_link
from protocol.协议仿真 import ControllerBehavior, ProtocolActionBridge, VirtualStm32
from mission.任务计划 import MaterialStatus, TaskPlan, TaskRuntimeConfig
from mission.视觉任务状态机 import TaskEvent, TaskState, TaskStateMachine
from tools.参数调试 import save_config, update_hsv_range
from vision.站点定位 import AprilTagStationDetector


class Stage2Tests(unittest.TestCase):
    def test_preflight_accepts_current_project(self):
        report = run_preflight()
        self.assertTrue(report["passed"], report["errors"])

    def test_offline_evaluation_covers_normal_and_fault_scenarios(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ApplicationConfig(
                task_path=Path(__file__).resolve().parents[1] / "config" / "仿真任务.json",
                log_path=Path(directory) / "evaluation.actions.jsonl",
            )
            results = evaluate(config)
            self.assertEqual(len(results), 5)
            self.assertTrue(all(result.passed for result in results))
            self.assertEqual(results[0].observed, "complete")

    def test_safety_monitor_blocks_sensor_faults(self):
        monitor = SafetyMonitor(minimum_tof_mm=80)
        self.assertFalse(monitor.check("approach_target", VirtualSensors(tof_distance_mm=50)).allowed)
        self.assertFalse(monitor.check("approach_target", VirtualSensors(limit_triggered=True)).allowed)
        self.assertFalse(monitor.check("assemble_material", VirtualSensors()).allowed)
        self.assertTrue(monitor.check("approach_target", VirtualSensors(tof_distance_mm=100)).allowed)

    def test_integrated_runtime_connects_vision_scanner_and_mission(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "runtime.jsonl"
            config = ApplicationConfig(
                task_path=Path(__file__).resolve().parents[1] / "config" / "仿真任务.json",
                log_path=log_path,
            )
            runtime = create_simulation_runtime(config)
            self.assertEqual(runtime.run(), MissionState.COMPLETE)
            self.assertGreaterEqual(len(runtime.snapshots), 8)
            self.assertTrue(any(snapshot.target_count > 0 for snapshot in runtime.snapshots))
            self.assertTrue(all(snapshot.station_count > 0 for snapshot in runtime.snapshots))

    def test_apriltag_station_detection_works_without_camera_calibration(self):
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        marker = cv2.aruco.generateImageMarker(dictionary, 1, 160)
        image = np.full((240, 240), 255, dtype=np.uint8)
        image[40:200, 40:200] = marker
        detector = AprilTagStationDetector({1: "material_area"}, tag_size_mm=40)
        detections = detector.detect(image)
        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].station_id, "material_area")
        self.assertAlmostEqual(detections[0].center_px[0], 119.5, delta=2.0)
        self.assertIsNone(detections[0].tvec)

    def test_virtual_devices_and_memory_transport_are_deterministic(self):
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        camera = VirtualCamera([frame])
        ok, replayed = camera.read()
        self.assertTrue(ok)
        self.assertEqual(replayed.shape, (8, 8, 3))
        self.assertFalse(camera.read()[0])

        scan = ScanResult(ScanType.CODE128, "SIM-001", True, "virtual")
        scanner = VirtualScanner([scan])
        self.assertEqual(scanner.read(), scan)
        self.assertIsNone(scanner.read())

        sensors = VirtualSensors(tof_distance_mm=120.0, grasp_confirmed=True)
        self.assertEqual(sensors.safety_snapshot()["tof_distance_mm"], 120.0)
        self.assertTrue(sensors.safety_snapshot()["grasp_confirmed"])

        left, right = create_memory_link()
        packet = Frame(MessageType.PING, b"test", sequence=7).encode()
        self.assertEqual(left.write(packet), len(packet))
        self.assertEqual(right.read_available(), packet)

    def test_protocol_action_bridge_completes_over_memory_serial(self):
        pi_transport, stm32_transport = create_memory_link()
        endpoint = ProtocolEndpoint(ack_timeout_ms=100, max_retries=1)
        bridge = ProtocolActionBridge(endpoint, pi_transport, stm32_transport)
        controller = VirtualStm32(ControllerBehavior.NORMAL)
        self.assertTrue(bridge.send_action("place_ring", b"{}", 0.0))
        bridge.exchange(controller, 1.0)
        self.assertTrue(endpoint.pending.completed)

        failing_controller = VirtualStm32(ControllerBehavior.FAIL)
        endpoint.pending = None
        self.assertTrue(bridge.send_action("place_ring", b"{}", 10.0))
        bridge.exchange(failing_controller, 11.0)
        self.assertTrue(endpoint.pending.failed)
        self.assertEqual(endpoint.pending.error_code, 0x21)

    def test_protocol_action_bridge_retries_when_done_is_dropped(self):
        pi_transport, stm32_transport = create_memory_link()
        endpoint = ProtocolEndpoint(ack_timeout_ms=10, max_retries=1)
        bridge = ProtocolActionBridge(endpoint, pi_transport, stm32_transport)
        controller = VirtualStm32(ControllerBehavior.DROP_DONE)
        bridge.send_action("return_home", b"{}", 0.0)
        bridge.exchange(controller, 1.0)
        self.assertTrue(endpoint.pending.acknowledged)
        self.assertFalse(endpoint.pending.completed)
        retry_packets = endpoint.poll(11.0)
        self.assertEqual(len(retry_packets), 2)  # heartbeat plus one retry

    def test_application_simulation_completes_and_writes_action_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "application.jsonl"
            config = ApplicationConfig(
                task_path=Path(__file__).resolve().parents[1] / "config" / "仿真任务.json",
                log_path=log_path,
            )
            state = SimulationRunner(config, ActionLogger(log_path)).run()
            self.assertEqual(state.value, "complete")
            records = log_path.read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(len(records), 10)
            self.assertIn('"action":"task_complete"', records[-1])

    def target(self, x=100.0, y=200.0, target_id=7):
        return {"target_id": target_id, "color": "red", "valid": True, "confidence": 0.9, "world_mm": [x, y]}

    def test_tuning_updates_and_saves_real_config(self):
        config = {"colors": {"red": {"ranges": [[[0, 0, 0], [179, 255, 255]]]}}}
        update_hsv_range(config, "red", 0, (10, 30, 20, 200, 30, 220))
        self.assertEqual(config["colors"]["red"]["ranges"][0], [[10, 20, 30], [30, 200, 220]])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "config.json")
            save_config(config, path)
            self.assertTrue(Path(path).exists())

    def test_frame_parser_handles_fragmentation_and_crc(self):
        packet = Frame(MessageType.GRASP, b"abc").encode()
        parser = FrameParser()
        self.assertEqual(parser.feed(packet[:4]), [])
        frames = parser.feed(packet[4:])
        self.assertEqual(frames, [Frame(MessageType.GRASP, b"abc")])
        corrupted = bytearray(packet)
        corrupted[4] ^= 1
        self.assertEqual(parser.feed(bytes(corrupted)), [])
        self.assertEqual(parser.invalid_frames, 1)
        self.assertEqual(crc16(b"test"), 0xDC2E)

    def test_protocol_ack_done_retry_and_heartbeat(self):
        endpoint = ProtocolEndpoint(heartbeat_interval_ms=100, ack_timeout_ms=50, max_retries=1)
        heartbeat = endpoint.poll(0)
        self.assertEqual(FrameParser().feed(heartbeat[0])[0].message_type, MessageType.HEARTBEAT)
        command_packet = endpoint.send_command(MessageType.GRASP, b"x", 0)
        self.assertEqual(len(endpoint.poll(49)), 0)
        retry = endpoint.poll(50)
        self.assertEqual(len(retry), 1)
        sequence = command_packet[3]
        endpoint.receive(Frame(MessageType.ACK, sequence=sequence).encode(), 60)
        endpoint.receive(Frame(MessageType.DONE, sequence=sequence).encode(), 70)
        self.assertTrue(endpoint.pending.completed)

    def test_task_action_is_encoded_as_protocol_command(self):
        endpoint = ProtocolEndpoint()
        packet = endpoint.send_action("grasp", {"coordinate_mm": [100, 200]}, 0)
        frame = FrameParser().feed(packet)[0]
        self.assertEqual(frame.message_type, MessageType.GRASP)
        self.assertIn(b"coordinate_mm", frame.payload)
        self.assertIsNone(endpoint.send_action("task_complete", {}, 1))

    def test_protocol_online_window_expires(self):
        endpoint = ProtocolEndpoint(offline_timeout_ms=100)
        endpoint.receive(Frame(MessageType.HEARTBEAT).encode(), 10)
        self.assertTrue(endpoint.is_online(100))
        self.assertFalse(endpoint.is_online(111))

    def test_fine_localization_rejects_jump_and_confirms_stable_mean(self):
        localizer = FineLocalizer(required_samples=3, max_spread_mm=2, max_step_mm=20, target_id=7)
        localizer.reset(0)
        self.assertEqual(localizer.update_from_target(self.target(100, 200), 10).status, LocalizationStatus.COLLECTING)
        self.assertEqual(localizer.update_from_target(self.target(130, 200), 20).status, LocalizationStatus.JUMP_REJECTED)
        localizer.reset(0)
        localizer.update_from_target(self.target(100.0, 200.0), 10)
        localizer.update_from_target(self.target(101.0, 201.0), 20)
        result = localizer.update_from_target(self.target(100.5, 200.5), 30)
        self.assertEqual(result.status, LocalizationStatus.STABLE)
        self.assertEqual(result.coordinate_mm, (100.5, 200.5))

    def test_state_machine_completes_one_task_and_recovers_on_timeout(self):
        localizer = FineLocalizer(required_samples=2, max_spread_mm=2, max_step_mm=20, target_id=7)
        machine = TaskStateMachine(localizer, state_timeout_ms=100, max_recovery_attempts=1)
        self.assertEqual(machine.handle(TaskEvent("start"), 0)[0].name, "search_target")
        self.assertEqual(machine.state, TaskState.SEARCHING)
        machine.handle(TaskEvent("vision_update", {"targets": [self.target()]}), 10)
        machine.handle(TaskEvent("arrived_near_target"), 20)
        vision = {"targets": [self.target(100, 200)]}
        machine.handle(TaskEvent("vision_update", vision), 30)
        machine.handle(TaskEvent("vision_update", {"targets": [self.target(101, 201)]}), 40)
        self.assertEqual(machine.state, TaskState.GRASPING)
        machine.handle(TaskEvent("grasp_done"), 50)
        machine.handle(TaskEvent("grasp_verified"), 60)
        machine.handle(TaskEvent("place_done"), 70)
        machine.handle(TaskEvent("place_verified"), 80)
        self.assertEqual(machine.state, TaskState.COMPLETE)
        machine.handle(TaskEvent("reset"), 90)
        machine.handle(TaskEvent("start"), 100)
        self.assertEqual(machine.tick(201)[0].name, "recover")

    def test_protocol_version_sequence_and_fail_code(self):
        endpoint = ProtocolEndpoint()
        packet = endpoint.send_command(MessageType.GRASP, b"x", 0)
        frame = FrameParser().feed(packet)[0]
        self.assertEqual((frame.version, frame.sequence, frame.message_type), (1, packet[3], MessageType.GRASP))
        endpoint.receive(Frame(MessageType.FAIL, bytes([7]), sequence=frame.sequence).encode(), 10)
        self.assertTrue(endpoint.pending.failed)
        self.assertEqual(endpoint.pending.error_code, 7)

    def test_external_scanner_and_task_qr_validation(self):
        parser = ExternalScannerParser(ScanType.CODE128)
        result = parser.feed(b"1\r\n2\n")[0]
        self.assertTrue(result.valid)
        qr = ScanResult(
            ScanType.TASK_QR,
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}',
            True,
            "test",
        )
        instruction = parse_task_qr(qr)
        self.assertEqual(instruction.task_id, "T1")
        self.assertEqual(instruction.first_batch[0].color, "red")

    def test_task_plan_rejects_invalid_material_transition(self):
        qr = ScanResult(ScanType.TASK_QR, '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],"second_batch":[]}', True, "test")
        plan = TaskPlan(parse_task_qr(qr))
        plan.update("F1", MaterialStatus.ON_ROBOT)
        with self.assertRaises(ValueError):
            plan.update("F1", MaterialStatus.ASSEMBLED)

    def test_runtime_config_loads_station_and_barcode_mapping(self):
        config_path = Path(__file__).resolve().parents[1] / "config" / "任务配置.json"
        config = TaskRuntimeConfig.from_file(config_path)
        self.assertEqual(config.barcode_mapping["1"], (0.0, 0.0))
        self.assertEqual(config.stations["finished"].station_id, "finished")

    def test_mission_state_machine_processes_two_batches(self):
        qr = ScanResult(ScanType.TASK_QR, '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}', True, "test")
        machine = MissionStateMachine()
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": qr}), 2)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        machine.handle(MissionEvent("material_loaded"), 3)
        machine.handle(MissionEvent("ring_placed"), 4)
        self.assertEqual(machine.state, MissionState.S4_SECOND_BATCH_ASSEMBLY)
        machine.handle(MissionEvent("material_loaded"), 5)
        machine.handle(MissionEvent("assembly_done"), 6)
        machine.handle(MissionEvent("home_confirmed"), 7)
        self.assertEqual(machine.state, MissionState.COMPLETE)


if __name__ == "__main__":
    unittest.main()
