#!/usr/bin/env python3
"""
EhViewer漫画管理工具 V3 - 支持数据库同步更新
在V2基础上增加:
1. 删除已移动漫画的数据库记录
2. 备份修改后的数据库
3. 推送回手机供导入
"""

import os
import sys
import sqlite3
import shutil
import argparse
from pathlib import Path
from typing import List, Dict, Optional
import subprocess
import platform
import time
from datetime import datetime

# 配置常量
SPIDER_INFO_FILENAME = ".ehviewer"
EHVIEWER_PACKAGE = "com.xjs.ehviewer"
DEFAULT_THRESHOLD = 0.9

EXPORT_DB_DIR = "/storage/emulated/0/EhViewer/data"
DOWNLOAD_DIR = "/storage/emulated/0/EhViewer/download"


class SpiderInfo:
    """解析.ehviewer文件中的阅读进度信息"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.version = -1
        self.start_page = 0
        self.gid = -1
        self.token = None
        self.preview_pages = -1
        self.preview_per_page = -1
        self.pages = -1
        self.ptoken_map = {}

    @staticmethod
    def _get_start_page(hex_str: str) -> int:
        if not hex_str:
            return 0
        try:
            return max(int(hex_str, 16), 0)
        except ValueError:
            return 0

    @staticmethod
    def _get_version(line: str) -> int:
        if not line:
            return -1
        if line.startswith("VERSION"):
            try:
                return int(line[7:])
            except ValueError:
                return -1
        return 1

    def read(self) -> bool:
        try:
            with open(self.file_path, 'r', encoding='ascii') as f:
                lines = f.readlines()

            if not lines:
                return False

            idx = 0
            self.version = self._get_version(lines[idx].strip())

            if self.version == 2:
                idx += 1
                self.start_page = self._get_start_page(lines[idx].strip())
            elif self.version == 1:
                self.start_page = self._get_start_page(lines[idx].strip())
            else:
                return False

            idx += 1
            self.gid = int(lines[idx].strip())
            idx += 1
            self.token = lines[idx].strip()
            idx += 1
            idx += 1
            self.preview_pages = int(lines[idx].strip())
            idx += 1

            if self.version == 2:
                self.preview_per_page = int(lines[idx].strip())
                idx += 1

            self.pages = int(lines[idx].strip())
            idx += 1

            if self.pages <= 0:
                return False

            for i in range(idx, len(lines)):
                line = lines[i].strip()
                if ' ' in line:
                    parts = line.split(' ', 1)
                    if len(parts) == 2:
                        page_idx = int(parts[0])
                        ptoken = parts[1]
                        if ptoken and ptoken != "failed":
                            self.ptoken_map[page_idx] = ptoken

            return self.gid != -1 and self.token and self.pages > 0

        except (IOError, ValueError, IndexError) as e:
            print(f"读取{self.file_path}失败: {e}")
            return False

    def get_read_progress(self) -> float:
        if self.pages <= 0:
            return 0.0
        return (self.start_page + 1) / self.pages


class ADBManager:
    """ADB操作管理器"""

    def __init__(self):
        self.is_windows = platform.system() == "Windows"
        self.device_id = None

    def check_adb(self) -> bool:
        try:
            subprocess.run(['adb', 'version'],
                         capture_output=True, text=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("错误: 未找到adb命令")
            return False

    def check_device(self) -> bool:
        try:
            result = subprocess.run(['adb', 'devices'],
                                  capture_output=True, text=True, check=True)
            lines = result.stdout.strip().split('\n')
            devices = [line.split('\t')[0] for line in lines[1:] if '\tdevice' in line]

            if not devices:
                print("错误: 未检测到设备")
                return False

            if len(devices) > 1:
                print(f"检测到多个设备: {devices}")
                return False

            self.device_id = devices[0]
            print(f"✓ 已连接设备: {self.device_id}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"检查设备失败: {e}")
            return False

    def pull_exported_database(self, local_path: str) -> bool:
        try:
            result = subprocess.run(['adb', 'shell', 'ls', '-t', EXPORT_DB_DIR],
                                  capture_output=True, text=True)

            if result.returncode != 0:
                print(f"错误: 无法访问 {EXPORT_DB_DIR}")
                print("\n请在手机EhViewer中导出数据库: 设置 -> 高级 -> 导出数据")
                return False

            files = [f.strip() for f in result.stdout.split('\n') if f.strip().endswith('.db')]

            if not files:
                print(f"错误: {EXPORT_DB_DIR} 中没有数据库文件")
                print("\n请在手机EhViewer中导出数据库: 设置 -> 高级 -> 导出数据")
                return False

            latest_db = files[0]
            remote_path = f"{EXPORT_DB_DIR}/{latest_db}"

            print(f"找到数据库: {latest_db}")
            print(f"正在拉取...")

            subprocess.run(['adb', 'pull', remote_path, local_path],
                         capture_output=True, text=True, check=True)

            print(f"✓ 数据库已拉取")
            return True

        except subprocess.CalledProcessError as e:
            print(f"拉取数据库失败: {e}")
            return False

    def push_database_to_phone(self, local_db_path: str) -> bool:
        """推送修改后的数据库到手机公共存储"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            filename = f"ehviewer_cleaned_{timestamp}.db"
            remote_path = f"{EXPORT_DB_DIR}/{filename}"

            print(f"\n正在推送更新后的数据库到手机...")
            print(f"目标路径: {remote_path}")

            subprocess.run(['adb', 'push', local_db_path, remote_path],
                         capture_output=True, text=True, check=True)

            print(f"✓ 数据库已推送到手机")
            print(f"\n📱 请在手机EhViewer中导入数据库:")
            print(f"   设置 → 高级 → 导入数据 → 选择 {filename}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"推送数据库失败: {e}")
            return False

    def list_manga_dirs(self) -> List[str]:
        try:
            result = subprocess.run(['adb', 'shell', 'ls', '-1', DOWNLOAD_DIR],
                                  capture_output=True, text=True, check=True)
            dirs = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
            return dirs
        except subprocess.CalledProcessError as e:
            print(f"列出漫画目录失败: {e}")
            return []

    def pull_manga(self, manga_dirname: str, dest_path: str) -> bool:
        source = f"{DOWNLOAD_DIR}/{manga_dirname}"
        try:
            subprocess.run(['adb', 'pull', source, dest_path],
                         capture_output=True, text=True, check=True)
            print(f"  ✓ 已拉取: {manga_dirname}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ✗ 拉取失败: {e}")
            return False

    def remove_manga_dir(self, manga_dirname: str) -> bool:
        source = f"{DOWNLOAD_DIR}/{manga_dirname}"
        try:
            subprocess.run(['adb', 'shell', f'rm -rf "{source}"'],
                         capture_output=True, text=True, check=True)
            print(f"  ✓ 已从手机删除: {manga_dirname}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ✗ 删除失败: {e}")
            return False

    def check_manga_exists(self, manga_dirname: str) -> bool:
        source = f"{DOWNLOAD_DIR}/{manga_dirname}"
        try:
            result = subprocess.run(['adb', 'shell', f'test -d "{source}"'],
                                  capture_output=True, text=True)
            return result.returncode == 0
        except subprocess.CalledProcessError:
            return False


class MangaDatabase:
    """漫画数据库管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    def connect(self) -> bool:
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            return True
        except sqlite3.Error as e:
            print(f"连接数据库失败: {e}")
            return False

    def close(self):
        if self.conn:
            self.conn.close()

    def backup(self, backup_path: str) -> bool:
        """备份数据库"""
        try:
            shutil.copy2(self.db_path, backup_path)
            print(f"✓ 数据库已备份到: {backup_path}")
            return True
        except Exception as e:
            print(f"备份数据库失败: {e}")
            return False

    def get_all_downloads(self) -> List[Dict]:
        if not self.conn:
            return []

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT GID, TOKEN, TITLE, STATE, LEGACY, TIME, LABEL
                FROM DOWNLOADS
                ORDER BY TIME DESC
            """)

            results = []
            for row in cursor.fetchall():
                results.append({
                    'gid': row['GID'],
                    'token': row['TOKEN'],
                    'title': row['TITLE'],
                    'state': row['STATE'],
                    'legacy': row['LEGACY'],
                    'time': row['TIME'],
                    'label': row['LABEL']
                })

            return results

        except sqlite3.Error as e:
            print(f"查询下载记录失败: {e}")
            return []

    def get_download_dirname(self, gid: int) -> Optional[str]:
        if not self.conn:
            return None

        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT DIRNAME FROM DOWNLOAD_DIRNAME WHERE GID = ?", (gid,))
            row = cursor.fetchone()
            return row['DIRNAME'] if row else None
        except sqlite3.Error:
            return None

    def delete_download_by_gid(self, gid: int) -> bool:
        """删除指定GID的下载记录及相关数据"""
        if not self.conn:
            return False

        try:
            cursor = self.conn.cursor()

            # 删除主下载记录
            cursor.execute("DELETE FROM DOWNLOADS WHERE GID = ?", (gid,))
            deleted_downloads = cursor.rowcount

            # 删除目录名映射
            cursor.execute("DELETE FROM DOWNLOAD_DIRNAME WHERE GID = ?", (gid,))

            # 删除标签信息 (如果存在)
            try:
                cursor.execute("DELETE FROM GALLERY_TAGS WHERE GID = ?", (gid,))
            except sqlite3.Error:
                pass  # GALLERY_TAGS表可能不存在

            self.conn.commit()
            return deleted_downloads > 0

        except sqlite3.Error as e:
            print(f"  ✗ 删除记录失败 (GID={gid}): {e}")
            self.conn.rollback()
            return False

    def get_statistics(self) -> Dict:
        """获取数据库统计信息"""
        if not self.conn:
            return {}

        try:
            cursor = self.conn.cursor()
            stats = {}

            # 总下载数
            cursor.execute("SELECT COUNT(*) as count FROM DOWNLOADS")
            stats['total_downloads'] = cursor.fetchone()['count']

            # 各状态统计
            cursor.execute("""
                SELECT STATE, COUNT(*) as count
                FROM DOWNLOADS
                GROUP BY STATE
            """)
            stats['by_state'] = {row['STATE']: row['count'] for row in cursor.fetchall()}

            # 标签统计
            cursor.execute("SELECT COUNT(DISTINCT LABEL) as count FROM DOWNLOADS WHERE LABEL IS NOT NULL")
            stats['total_labels'] = cursor.fetchone()['count']

            return stats

        except sqlite3.Error as e:
            print(f"获取统计信息失败: {e}")
            return {}


class MangaManager:
    """漫画管理器主类 - V3版本"""

    def __init__(self):
        self.adb = ADBManager()
        self.db = None
        self.temp_db_path = "temp_ehviewer.db"
        self.backup_db_path = None

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

    def analyze_reading_progress(self, threshold: float = DEFAULT_THRESHOLD) -> List[Dict]:
        downloads = self.db.get_all_downloads()

        results = []
        print(f"\n开始分析 {len(downloads)} 个下载项的阅读进度...")
        print(f"阅读进度阈值: {threshold * 100:.0f}%\n")

        for dl in downloads:
            gid = dl['gid']
            title = dl['title']
            state = dl['state']

            dirname = self.db.get_download_dirname(gid)
            if not dirname:
                dirname = f"{gid}-{dl['token']}"

            if not self.adb.check_manga_exists(dirname):
                print(f"⚠ [{title}] 目录不存在,跳过")
                continue

            temp_dir = f"temp_manga_{gid}"
            os.makedirs(temp_dir, exist_ok=True)

            spider_info_path = os.path.join(temp_dir, SPIDER_INFO_FILENAME)
            remote_path = f"{DOWNLOAD_DIR}/{dirname}/{SPIDER_INFO_FILENAME}"

            try:
                result = subprocess.run(['adb', 'pull', remote_path, spider_info_path],
                                      capture_output=True, text=True)

                if result.returncode != 0 or not os.path.exists(spider_info_path):
                    print(f"✗ [{title}] 无法读取阅读进度")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    continue

                spider_info = SpiderInfo(spider_info_path)
                if spider_info.read():
                    progress = spider_info.get_read_progress()
                    state_text = {0: '无', 1: '等待', 2: '下载中', 3: '完成', 4: '失败'}.get(state, '未知')

                    info = {
                        'gid': gid,
                        'title': title,
                        'dirname': dirname,
                        'current_page': spider_info.start_page,
                        'total_pages': spider_info.pages,
                        'progress': progress,
                        'state': state,
                        'state_text': state_text
                    }

                    if progress >= threshold:
                        results.append(info)
                        print(f"✓ [{title}] 进度: {progress*100:.1f}% ({spider_info.start_page + 1}/{spider_info.pages}) 状态:{state_text}")
                    else:
                        print(f"  [{title}] 进度: {progress*100:.1f}% (未达阈值)")
                else:
                    print(f"✗ [{title}] 解析失败")

                shutil.rmtree(temp_dir, ignore_errors=True)

            except Exception as e:
                print(f"✗ [{title}] 处理失败: {e}")
                shutil.rmtree(temp_dir, ignore_errors=True)

        return results

    def move_manga_to_pc(self, manga_info: Dict, dest_dir: str, remove_from_phone: bool = False) -> bool:
        dirname = manga_info['dirname']
        title = manga_info['title']

        dest_path = os.path.join(dest_dir, dirname)

        print(f"\n正在处理: {title}")
        print(f"  进度: {manga_info['progress']*100:.1f}%")

        if not self.adb.pull_manga(dirname, dest_path):
            return False

        if remove_from_phone:
            if not self.adb.remove_manga_dir(dirname):
                print(f"  ⚠ 删除手机文件失败")

        return True

    def clean_database_records(self, gid_list: List[int]) -> int:
        """清理数据库记录并返回成功删除的数量"""
        if not gid_list:
            return 0

        print(f"\n正在清理 {len(gid_list)} 个漫画的数据库记录...")

        deleted_count = 0
        for gid in gid_list:
            if self.db.delete_download_by_gid(gid):
                deleted_count += 1
                print(f"  ✓ 已删除 GID={gid} 的记录")
            else:
                print(f"  ✗ 删除 GID={gid} 失败")

        return deleted_count

    def find_missing_manga(self) -> List[Dict]:
        """查找数据库中存在但手机上不存在的漫画"""
        downloads = self.db.get_all_downloads()

        missing = []
        print(f"\n开始检查 {len(downloads)} 个下载项...")

        for dl in downloads:
            gid = dl['gid']
            title = dl['title']

            # 获取目录名
            dirname = self.db.get_download_dirname(gid)
            if not dirname:
                dirname = f"{gid}-{dl['token']}"

            # 检查目录是否存在
            if not self.adb.check_manga_exists(dirname):
                state_text = {0: '无', 1: '等待', 2: '下载中', 3: '完成', 4: '失败'}.get(dl['state'], '未知')
                missing.append({
                    'gid': gid,
                    'title': title,
                    'dirname': dirname,
                    'state': dl['state'],
                    'state_text': state_text
                })
                print(f"✗ [{title}] 目录不存在 (GID: {gid})")
            else:
                print(f"✓ [{title}] 目录存在")

        return missing

    def create_backup_and_push(self) -> bool:
        """创建备份并推送到手机"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.backup_db_path = f"ehviewer_backup_{timestamp}.db"

        print(f"\n正在备份数据库...")
        if not self.db.backup(self.backup_db_path):
            return False

        print(f"\n正在推送更新后的数据库到手机...")
        return self.adb.push_database_to_phone(self.temp_db_path)


def main():
    parser = argparse.ArgumentParser(
        description='EhViewer漫画管理工具 V3 - 支持数据库同步',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
V3新功能:
  • 自动清理已移动漫画的数据库记录
  • 备份修改后的数据库
  • 推送到手机供导入

使用流程:
  1. 在手机导出数据库 (设置 -> 高级 -> 导出数据)
  2. 运行脚本移动漫画并清理记录
  3. 在手机导入更新后的数据库 (设置 -> 高级 -> 导入数据)

示例:
  # 分析
  %(prog)s analyze

  # 移动并自动清理数据库
  %(prog)s move --dest ~/Backup --threshold 0.9 --remove --sync-db

  # 查看数据库统计
  %(prog)s stats

  # 自动检测并清理不存在的漫画
  %(prog)s clean --push

  # 手动清理指定GID
  %(prog)s clean --gids 123456 789012 --push
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # analyze命令
    analyze_parser = subparsers.add_parser('analyze', help='分析漫画阅读进度')
    analyze_parser.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD,
                               help=f'阅读进度阈值 (默认: {DEFAULT_THRESHOLD})')

    # move命令
    move_parser = subparsers.add_parser('move', help='移动已读漫画到电脑')
    move_parser.add_argument('--dest', required=True, help='目标目录')
    move_parser.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD,
                            help=f'阅读进度阈值 (默认: {DEFAULT_THRESHOLD})')
    move_parser.add_argument('--remove', action='store_true',
                            help='移动后从手机删除原文件')
    move_parser.add_argument('--sync-db', action='store_true',
                            help='同步更新数据库(删除已移动漫画的记录)')
    move_parser.add_argument('--batch-size', type=int, default=0,
                            help='每批次移动数量(0=全部)')

    # stats命令
    stats_parser = subparsers.add_parser('stats', help='查看数据库统计信息')

    # clean命令 (手动清理)
    clean_parser = subparsers.add_parser('clean', help='清理数据库记录')
    clean_parser.add_argument('--gids', nargs='+', type=int,
                             help='要清理的漫画GID列表 (不指定则自动检测不存在的漫画)')
    clean_parser.add_argument('--push', action='store_true',
                             help='清理后推送数据库到手机')
    clean_parser.add_argument('--auto', action='store_true',
                             help='自动清理不存在的漫画(不需要确认)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    manager = MangaManager()

    try:
        if not manager.initialize():
            return 1

        if args.command == 'analyze':
            results = manager.analyze_reading_progress(args.threshold)

            print(f"\n{'='*60}")
            print(f"找到 {len(results)} 个阅读进度 >= {args.threshold*100:.0f}% 的漫画:")
            print(f"{'='*60}\n")

            for manga in results:
                print(f"标题: {manga['title']}")
                print(f"  GID: {manga['gid']}")
                print(f"  目录: {manga['dirname']}")
                print(f"  进度: {manga['progress']*100:.1f}% ({manga['current_page']+1}/{manga['total_pages']})")
                print(f"  状态: {manga['state_text']}")
                print()

        elif args.command == 'stats':
            stats = manager.db.get_statistics()

            print(f"\n{'='*60}")
            print("数据库统计信息")
            print(f"{'='*60}\n")

            print(f"总下载数: {stats.get('total_downloads', 0)}")
            print(f"\n按状态分类:")
            state_names = {0: '无', 1: '等待', 2: '下载中', 3: '完成', 4: '失败'}
            for state, count in stats.get('by_state', {}).items():
                print(f"  {state_names.get(state, '未知')}: {count}")
            print(f"\n总分组数: {stats.get('total_labels', 0)}")

        elif args.command == 'move':
            dest_dir = args.dest
            if not os.path.exists(dest_dir):
                print(f"创建目标目录: {dest_dir}")
                os.makedirs(dest_dir, exist_ok=True)

            results = manager.analyze_reading_progress(args.threshold)

            if not results:
                print(f"\n没有找到阅读进度 >= {args.threshold*100:.0f}% 的漫画")
                return 0

            batch_size = args.batch_size if args.batch_size > 0 else len(results)
            to_process = results[:batch_size]

            print(f"\n将移动 {len(to_process)} 个漫画到 {dest_dir}")
            if args.remove:
                print("⚠ 警告: 将从手机删除原文件!")
            if args.sync_db:
                print("✓ 将同步更新数据库记录")

            response = input("\n确认继续? (y/N): ")
            if response.lower() != 'y':
                print("已取消")
                return 0

            moved_gids = []
            for manga in to_process:
                if manager.move_manga_to_pc(manga, dest_dir, args.remove):
                    moved_gids.append(manga['gid'])

            print(f"\n✓ 成功移动 {len(moved_gids)}/{len(to_process)} 个漫画")

            # 同步数据库
            if args.sync_db and moved_gids:
                deleted_count = manager.clean_database_records(moved_gids)
                print(f"\n✓ 成功清理 {deleted_count}/{len(moved_gids)} 条记录")

                if manager.create_backup_and_push():
                    print(f"\n🎉 完成! 数据库已更新并推送到手机")
                    print(f"   备份文件: {manager.backup_db_path}")
                else:
                    print(f"\n⚠ 数据库推送失败,但本地备份已保存")

        elif args.command == 'clean':
            # 如果没有指定gids，则自动检测不存在的漫画
            if args.gids:
                gids_to_clean = args.gids
                print(f"\n将清理 {len(gids_to_clean)} 个指定GID的数据库记录")
                print(f"GIDs: {', '.join(map(str, gids_to_clean))}")
            else:
                print("未指定GID，将自动检测不存在的漫画...")
                missing_manga = manager.find_missing_manga()

                if not missing_manga:
                    print("\n✓ 所有数据库记录的漫画文件都存在，无需清理")
                    return 0

                gids_to_clean = [m['gid'] for m in missing_manga]

                print(f"\n{'='*60}")
                print(f"找到 {len(missing_manga)} 个不存在的漫画:")
                print(f"{'='*60}\n")

                for manga in missing_manga:
                    print(f"标题: {manga['title']}")
                    print(f"  GID: {manga['gid']}")
                    print(f"  目录: {manga['dirname']}")
                    print(f"  状态: {manga['state_text']}")
                    print()

            if args.push:
                print("✓ 清理后将推送到手机")

            # 如果不是自动模式，需要确认
            if not args.auto:
                response = input(f"\n确认清理 {len(gids_to_clean)} 条记录? (y/N): ")
                if response.lower() != 'y':
                    print("已取消")
                    return 0

            deleted_count = manager.clean_database_records(gids_to_clean)
            print(f"\n✓ 成功清理 {deleted_count}/{len(gids_to_clean)} 条记录")

            if args.push:
                if manager.create_backup_and_push():
                    print(f"\n🎉 完成! 数据库已更新并推送到手机")
                    print(f"   备份文件: {manager.backup_db_path}")
                else:
                    print(f"\n⚠ 数据库推送失败,但本地备份已保存")

        return 0

    finally:
        manager.cleanup()


if __name__ == '__main__':
    sys.exit(main())
