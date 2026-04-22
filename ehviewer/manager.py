"""漫画管理器主类 - 协调ADB、数据库和文件系统操作。"""

import os
import shutil
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

from .adb_manager import ADBManager
from .config import DOWNLOAD_DIR, SPIDER_INFO_FILENAME, DEFAULT_THRESHOLD, STATE_NAMES
from .database import MangaDatabase
from .filename_utils import sanitize_filename, needs_sanitization, make_name_mapping_note
from .spider_info import SpiderInfo


class MangaManager:
    """漫画管理器主类"""

    def __init__(self):
        self.adb = ADBManager()
        self.db: Optional[MangaDatabase] = None
        self.temp_db_path = "temp_ehviewer.db"
        self.backup_db_path: Optional[str] = None

    # ------------------------------------------------------------------
    # 初始化 / 清理
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        if not self.adb.check_adb():
            return False
        if not self.adb.check_device():
            return False
        if not self.adb.pull_exported_database(self.temp_db_path):
            return False

        self.db = MangaDatabase(self.temp_db_path)
        if not self.db.connect():
            return False

        return True

    def cleanup(self):
        if self.db:
            self.db.close()
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 阅读进度分析
    # ------------------------------------------------------------------

    def analyze_reading_progress(
        self, threshold: float = DEFAULT_THRESHOLD
    ) -> List[Dict]:
        """分析所有漫画的阅读进度, 返回超过阈值的列表。"""
        assert self.db is not None
        downloads = self.db.get_all_downloads()

        results = []
        print(f"\n开始分析 {len(downloads)} 个下载项的阅读进度...")
        print(f"阅读进度阈值: {threshold * 100:.0f}%\n")

        for dl in downloads:
            gid = dl["gid"]
            title = dl["title"]
            state = dl["state"]

            dirname = self.db.get_download_dirname(gid) or f"{gid}-{dl['token']}"

            if not self.adb.check_manga_exists(dirname):
                print(f"  [跳过] {title} — 手机上目录不存在")
                continue

            temp_dir = f"temp_manga_{gid}"
            os.makedirs(temp_dir, exist_ok=True)

            spider_info_path = os.path.join(temp_dir, SPIDER_INFO_FILENAME)
            remote_path = f"{DOWNLOAD_DIR}/{dirname}/{SPIDER_INFO_FILENAME}"

            try:
                ok = self.adb.pull_single_file(remote_path, spider_info_path)

                if not ok or not os.path.exists(spider_info_path):
                    print(f"  [失败] {title} — 无法读取阅读进度文件")
                    continue

                spider = SpiderInfo(spider_info_path)
                if spider.read():
                    progress = spider.get_read_progress()
                    state_text = STATE_NAMES.get(state, "未知")

                    info = {
                        "gid": gid,
                        "title": title,
                        "dirname": dirname,
                        "current_page": spider.start_page,
                        "total_pages": spider.pages,
                        "progress": progress,
                        "state": state,
                        "state_text": state_text,
                    }

                    if progress >= threshold:
                        results.append(info)
                        print(
                            f"  [达标] {title}"
                            f" {progress*100:.1f}%"
                            f" ({spider.start_page + 1}/{spider.pages})"
                            f" 状态:{state_text}"
                        )
                    else:
                        print(
                            f"  [未达] {title}"
                            f" {progress*100:.1f}%"
                            f" ({spider.start_page + 1}/{spider.pages})"
                        )
                else:
                    print(f"  [失败] {title} — 解析进度文件失败")

            except Exception as e:
                print(f"  [错误] {title} — {e}")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        return results

    # ------------------------------------------------------------------
    # 漫画移动
    # ------------------------------------------------------------------

    def _resolve_local_dirname(self, remote_dirname: str) -> tuple[str, bool]:
        """
        将手机上的目录名转换为本地安全的目录名。

        Returns:
            (local_dirname, was_sanitized)
        """
        local_dirname = sanitize_filename(remote_dirname)
        was_sanitized = local_dirname != remote_dirname
        return local_dirname, was_sanitized

    def move_manga_to_pc(
        self,
        manga_info: Dict,
        dest_dir: str,
        remove_from_phone: bool = False,
        dry_run: bool = False,
    ) -> bool:
        """
        将单个漫画从手机拉取到电脑。

        文件名净化策略:
        - remote_dirname 用于 adb pull 的源路径 (Android路径, 允许特殊字符)
        - local_dirname  是净化后的本地目录名 (确保Windows兼容)
        - 若两者不同, 会额外记录映射关系到 dest_dir/name_mapping.txt

        Args:
            manga_info:       analyze_reading_progress 返回的漫画信息字典
            dest_dir:         本地目标根目录
            remove_from_phone: 成功拉取后是否删除手机上的原文件
            dry_run:          仅打印操作计划, 不执行实际操作
        """
        remote_dirname = manga_info["dirname"]
        title = manga_info["title"]
        local_dirname, was_sanitized = self._resolve_local_dirname(remote_dirname)
        local_dest_path = os.path.join(dest_dir, local_dirname)

        print(f"\n  标题: {title}")
        print(f"  进度: {manga_info['progress']*100:.1f}%")
        print(f"  手机目录: {remote_dirname}")
        if was_sanitized:
            print(f"  本地目录: {local_dirname}  [已净化文件名]")
        else:
            print(f"  本地目录: {local_dirname}")

        if dry_run:
            print("  [DRY-RUN] 跳过实际操作")
            return True

        if os.path.exists(local_dest_path):
            print(f"  [跳过] 本地已存在: {local_dest_path}")
            return True

        if not self.adb.pull_manga(remote_dirname, local_dest_path):
            return False

        # 记录文件名映射 (供日后参考)
        if was_sanitized:
            self._append_name_mapping(dest_dir, remote_dirname, local_dirname)

        if remove_from_phone:
            if not self.adb.remove_manga_dir(remote_dirname):
                print("  [警告] 删除手机文件失败, 请手动清理")

        return True

    @staticmethod
    def _append_name_mapping(dest_dir: str, original: str, sanitized: str):
        """将文件名映射追加到 name_mapping.txt。"""
        mapping_file = os.path.join(dest_dir, "name_mapping.txt")
        line = f"{sanitized}\t<--\t{original}\n"
        try:
            with open(mapping_file, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # 映射记录不影响主流程

    # ------------------------------------------------------------------
    # 数据库清理
    # ------------------------------------------------------------------

    def clean_database_records(self, gid_list: List[int]) -> int:
        """清理数据库中指定GID的记录, 返回成功删除数量。"""
        assert self.db is not None
        if not gid_list:
            return 0

        print(f"\n正在清理 {len(gid_list)} 个漫画的数据库记录...")
        deleted_count = 0
        for gid in gid_list:
            if self.db.delete_download_by_gid(gid):
                deleted_count += 1
                print(f"  已删除记录 GID={gid}")
            else:
                print(f"  删除失败 GID={gid}")

        return deleted_count

    def find_missing_manga(self) -> List[Dict]:
        """查找数据库中存在但手机上不存在的漫画。"""
        assert self.db is not None
        downloads = self.db.get_all_downloads()
        missing = []
        print(f"\n开始检查 {len(downloads)} 个下载项...")

        for dl in downloads:
            gid = dl["gid"]
            title = dl["title"]
            dirname = self.db.get_download_dirname(gid) or f"{gid}-{dl['token']}"

            if not self.adb.check_manga_exists(dirname):
                state_text = STATE_NAMES.get(dl["state"], "未知")
                missing.append(
                    {
                        "gid": gid,
                        "title": title,
                        "dirname": dirname,
                        "state": dl["state"],
                        "state_text": state_text,
                    }
                )
                print(f"  [缺失] {title} (GID: {gid})")
            else:
                print(f"  [存在] {title}")

        return missing

    # ------------------------------------------------------------------
    # 数据库备份与推送
    # ------------------------------------------------------------------

    def create_backup_and_push(self) -> bool:
        """备份当前数据库并推送到手机。"""
        assert self.db is not None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.backup_db_path = f"ehviewer_backup_{timestamp}.db"

        print("\n正在备份数据库...")
        if not self.db.backup(self.backup_db_path):
            return False

        return self.adb.push_database_to_phone(self.temp_db_path)

    # ------------------------------------------------------------------
    # 文件名安全预检
    # ------------------------------------------------------------------

    def preview_filename_issues(self) -> List[Dict]:
        """
        预扫描所有漫画目录名, 列出在Windows上需要净化的条目。
        不连接手机, 直接分析数据库中的dirname字段。
        """
        assert self.db is not None
        downloads = self.db.get_all_downloads()
        issues = []

        for dl in downloads:
            gid = dl["gid"]
            dirname = self.db.get_download_dirname(gid) or f"{gid}-{dl['token']}"
            if needs_sanitization(dirname):
                safe = sanitize_filename(dirname)
                issues.append(
                    {
                        "gid": gid,
                        "title": dl["title"],
                        "original": dirname,
                        "sanitized": safe,
                    }
                )

        return issues
