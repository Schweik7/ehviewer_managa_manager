"""ADB操作封装: 设备检测、数据库拉取/推送、漫画目录操作。"""

import os
import shutil
import subprocess
import platform
from datetime import datetime
from typing import List

from .config import EXPORT_DB_DIR, DOWNLOAD_DIR

# Windows中文系统默认GBK编码会导致解析adb(UTF-8输出)时出错, 统一指定UTF-8
_SUBPROCESS_KWARGS = dict(
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)


class ADBManager:
    """ADB操作管理器"""

    def __init__(self):
        self.is_windows = platform.system() == "Windows"
        self.device_id: str | None = None

    # ------------------------------------------------------------------
    # 基础检查
    # ------------------------------------------------------------------

    def check_adb(self) -> bool:
        try:
            subprocess.run(["adb", "version"], **_SUBPROCESS_KWARGS, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("错误: 未找到 adb 命令，请确认 Android SDK Platform-Tools 已安装并在 PATH 中")
            return False

    def check_device(self) -> bool:
        try:
            result = subprocess.run(["adb", "devices"], **_SUBPROCESS_KWARGS, check=True)
            lines = result.stdout.strip().split("\n")
            devices = [
                line.split("\t")[0] for line in lines[1:] if "\tdevice" in line
            ]

            if not devices:
                print("错误: 未检测到已连接的设备，请检查USB连接或无线调试是否开启")
                return False

            if len(devices) > 1:
                print(f"错误: 检测到多个设备，请只连接一台设备: {devices}")
                return False

            self.device_id = devices[0]
            print(f"已连接设备: {self.device_id}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"检查设备失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 数据库操作
    # ------------------------------------------------------------------

    def pull_exported_database(self, local_path: str) -> bool:
        """从手机拉取最新导出的数据库文件。"""
        try:
            result = subprocess.run(
                ["adb", "shell", "ls", "-t", EXPORT_DB_DIR],
                **_SUBPROCESS_KWARGS,
            )

            if result.returncode != 0:
                print(f"错误: 无法访问 {EXPORT_DB_DIR}")
                print("请先在手机 EhViewer 中导出数据库: 设置 → 高级 → 导出数据")
                return False

            files = [
                f.strip()
                for f in result.stdout.split("\n")
                if f.strip().endswith(".db")
            ]

            if not files:
                print(f"错误: {EXPORT_DB_DIR} 中没有数据库文件")
                print("请先在手机 EhViewer 中导出数据库: 设置 → 高级 → 导出数据")
                return False

            latest_db = files[0]
            remote_path = f"{EXPORT_DB_DIR}/{latest_db}"

            print(f"找到数据库: {latest_db}")
            print("正在拉取...")

            subprocess.run(
                ["adb", "pull", remote_path, local_path],
                **_SUBPROCESS_KWARGS,
                check=True,
            )

            print("数据库已拉取到本地")
            return True

        except subprocess.CalledProcessError as e:
            print(f"拉取数据库失败: {e}")
            return False

    def push_database_to_phone(self, local_db_path: str) -> bool:
        """推送修改后的数据库到手机公共存储。"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            filename = f"ehviewer_cleaned_{timestamp}.db"
            remote_path = f"{EXPORT_DB_DIR}/{filename}"

            print(f"\n正在推送更新后的数据库到手机...")
            print(f"目标路径: {remote_path}")

            subprocess.run(
                ["adb", "push", local_db_path, remote_path],
                **_SUBPROCESS_KWARGS,
                check=True,
            )

            print("数据库已推送到手机")
            print(f"\n请在手机 EhViewer 中导入数据库:")
            print(f"  设置 → 高级 → 导入数据 → 选择 {filename}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"推送数据库失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 漫画目录操作
    # ------------------------------------------------------------------

    def list_manga_dirs(self) -> List[str]:
        """列出手机下载目录中的所有漫画目录名。"""
        try:
            result = subprocess.run(
                ["adb", "shell", "ls", "-1", DOWNLOAD_DIR],
                **_SUBPROCESS_KWARGS,
                check=True,
            )
            return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        except subprocess.CalledProcessError as e:
            print(f"列出漫画目录失败: {e}")
            return []

    def pull_manga(self, remote_dirname: str, local_dest_path: str) -> bool:
        """
        从手机拉取单个漫画目录到本地。

        adb 在 Windows 上无法处理目标路径中的非 ASCII 字符（中文等），
        因此统一先拉到 ASCII 安全的临时目录，再用 Python 重命名到最终路径。
        NTFS 原生支持 Unicode 文件名，重命名不受此限制。

        Args:
            remote_dirname:  手机上的目录名 (可含特殊字符, 作为Android路径使用)
            local_dest_path: 本地目标路径 (已由调用方做过Windows非法字符净化)
        """
        source = f"{DOWNLOAD_DIR}/{remote_dirname}"
        parent_dir = os.path.dirname(local_dest_path)
        local_basename = os.path.basename(local_dest_path)

        # 提取 GID (dirname格式为 "<GID>-<title>") 构造 ASCII 临时目录名
        gid_part = local_basename.split("-")[0] if "-" in local_basename else local_basename[:12]
        temp_path = os.path.join(parent_dir, f"_pull_tmp_{gid_part}")

        # 清理可能的残留临时目录
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path, ignore_errors=True)

        try:
            result = subprocess.run(
                ["adb", "pull", source, temp_path],
                **_SUBPROCESS_KWARGS,
            )
            if result.returncode != 0:
                stderr = (result.stderr.strip() or result.stdout.strip()).split("\n")[0]
                print(f"  拉取失败: {stderr}")
                shutil.rmtree(temp_path, ignore_errors=True)
                return False

            # 重命名到最终目标路径 (NTFS 支持 Unicode)
            if os.path.exists(local_dest_path):
                shutil.rmtree(local_dest_path)
            os.rename(temp_path, local_dest_path)

            print(f"  已拉取到: {local_dest_path}")
            return True

        except OSError as e:
            print(f"  文件操作失败: {e}")
            shutil.rmtree(temp_path, ignore_errors=True)
            return False

    def remove_manga_dir(self, manga_dirname: str) -> bool:
        """从手机删除漫画目录。"""
        source = f"{DOWNLOAD_DIR}/{manga_dirname}"
        try:
            subprocess.run(
                ["adb", "shell", f'rm -rf "{source}"'],
                **_SUBPROCESS_KWARGS,
                check=True,
            )
            print(f"  已从手机删除: {manga_dirname}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  删除失败: {e}")
            return False

    def check_manga_exists(self, manga_dirname: str) -> bool:
        """检查手机上的漫画目录是否存在。"""
        source = f"{DOWNLOAD_DIR}/{manga_dirname}"
        try:
            result = subprocess.run(
                ["adb", "shell", f'test -d "{source}"'],
                **_SUBPROCESS_KWARGS,
            )
            return result.returncode == 0
        except subprocess.CalledProcessError:
            return False

    def pull_single_file(self, remote_path: str, local_path: str) -> bool:
        """从手机拉取单个文件。"""
        try:
            result = subprocess.run(
                ["adb", "pull", remote_path, local_path],
                **_SUBPROCESS_KWARGS,
            )
            return result.returncode == 0
        except subprocess.CalledProcessError:
            return False
