"""只用于调试。比赛镜像不安装这项登录自启动。

Install/remove this project's desktop autostart on Raspberry Pi (no sudo).
"""
import argparse
import os
from pathlib import Path
import sys


def desktop_quote(value):
    # Desktop Entry Exec quoting: literal %, backslash, quote, $, backtick.
    value = str(value).replace("%", "%%")
    value = value.replace("\\", "\\\\\\\\").replace('"', '\\"')
    value = value.replace("$", "\\$").replace("`", "\\`")
    return '"' + value + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=int, default=0, help="camera index")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if os.environ.get("VISION_MATCH_IMAGE") == "1":
        parser.error("比赛镜像不安装登录自启动")
    if sys.platform != "linux":
        parser.error("Run this installer on the Raspberry Pi")
    if os.geteuid() == 0:
        parser.error("Run as your desktop user, WITHOUT sudo")
    if args.source < 0:
        parser.error("Camera index must be non-negative")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not config.is_absolute():
        parser.error("XDG_CONFIG_HOME must be absolute")
    target = config / "autostart" / "vision-project-autostart.desktop"
    marker = "X-Vision-Project-Autostart=true"
    if target.exists() and marker not in target.read_text(encoding="utf-8"):
        parser.error(f"Unmanaged file exists; will not replace: {target}")
    if args.remove:
        target.unlink(missing_ok=True)
        print("Autostart removed. The currently running application is unchanged.")
        return
    root = Path(__file__).resolve().parent
    if any(c in str(root) for c in "\r\n"):
        parser.error("Project path cannot contain line breaks")
    if not (root / ".venv/bin/python").is_file():
        parser.error("Create the Raspberry Pi .venv and install dependencies first")
    for name in ("start.sh", "boot_vision.sh"):
        path = root / name
        if not path.is_file():
            parser.error(f"Missing {name}")
        # USB copies from Windows may contain CRLF; normalize only our launchers.
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "[Desktop Entry]\nType=Application\nName=Vision Project Autostart\n"
        f"Exec=/bin/bash {desktop_quote(root / 'boot_vision.sh')} {args.source}\n"
        "Terminal=false\nX-GNOME-Autostart-enabled=true\n" + marker + "\n",
        encoding="utf-8")
    print(f"Installed: {target}")
    print("Enable Desktop Autologin in sudo raspi-config, then reboot.")
    print(f"Startup errors: {root / 'logs/autostart.log'}")


if __name__ == "__main__":
    main()
