#!/usr/bin/env python3
"""
EhViewer漫画管理工具 - 入口

用法:
  python main.py analyze           # 分析阅读进度
  python main.py move --dest D:/Manga --batch-size 3 --dry-run  # 小批量预演
  python main.py move --dest D:/Manga --batch-size 3 --remove --sync-db  # 小批量实际执行
  python main.py move --dest D:/Manga --remove --sync-db        # 全量迁移
  python main.py check-names       # 预检文件名Windows兼容性
  python main.py stats             # 数据库统计
  python main.py clean --push      # 清理不存在漫画的数据库记录
"""

import os
import sys
import argparse

# Windows终端UTF-8输出修复
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ehviewer.config import DEFAULT_THRESHOLD, STATE_NAMES
from ehviewer.manager import MangaManager


def cmd_analyze(manager: MangaManager, args) -> int:
    results = manager.analyze_reading_progress(args.threshold)

    print(f"\n{'='*60}")
    print(f"找到 {len(results)} 个阅读进度 >= {args.threshold*100:.0f}% 的漫画:")
    print(f"{'='*60}\n")

    for manga in results:
        print(f"标题: {manga['title']}")
        print(f"  GID:   {manga['gid']}")
        print(f"  目录:  {manga['dirname']}")
        print(f"  进度:  {manga['progress']*100:.1f}%"
              f" ({manga['current_page']+1}/{manga['total_pages']})")
        print(f"  状态:  {manga['state_text']}")
        print()
    return 0


def cmd_check_names(manager: MangaManager, args) -> int:
    """预检数据库中所有dirname在Windows上的兼容性, 无需连接手机。"""
    issues = manager.preview_filename_issues()

    if not issues:
        print("\n所有漫画目录名均兼容Windows, 无需净化。")
        return 0

    print(f"\n{'='*60}")
    print(f"发现 {len(issues)} 个目录名需要净化 (含Windows非法字符):")
    print(f"{'='*60}\n")
    for item in issues:
        print(f"  GID:    {item['gid']}")
        print(f"  标题:   {item['title']}")
        print(f"  原始:   {item['original']}")
        print(f"  净化后: {item['sanitized']}")
        print()

    print("提示: 运行 move 命令时会自动应用净化, 并在目标目录生成 name_mapping.txt 记录映射关系。")
    return 0


def cmd_stats(manager: MangaManager, args) -> int:
    assert manager.db is not None
    stats = manager.db.get_statistics()

    print(f"\n{'='*60}")
    print("数据库统计信息")
    print(f"{'='*60}\n")
    print(f"总下载数: {stats.get('total_downloads', 0)}")
    print("\n按状态分类:")
    for state, count in stats.get("by_state", {}).items():
        print(f"  {STATE_NAMES.get(state, '未知')}: {count}")
    print(f"\n总分组数: {stats.get('total_labels', 0)}")
    return 0


def cmd_move(manager: MangaManager, args) -> int:
    dest_dir = args.dest
    dry_run: bool = args.dry_run

    if not dry_run and not os.path.exists(dest_dir):
        print(f"创建目标目录: {dest_dir}")
        os.makedirs(dest_dir, exist_ok=True)

    results = manager.analyze_reading_progress(args.threshold)

    if not results:
        print(f"\n没有找到阅读进度 >= {args.threshold*100:.0f}% 的漫画")
        return 0

    batch_size = args.batch_size if args.batch_size > 0 else len(results)
    to_process = results[:batch_size]

    print(f"\n{'='*60}")
    print(f"准备移动 {len(to_process)} 个漫画到: {dest_dir}")
    if dry_run:
        print("  模式: DRY-RUN (仅预演, 不执行实际操作)")
    if args.remove:
        print("  警告: 成功后将从手机删除原文件")
    if args.sync_db:
        print("  将同步更新EhViewer数据库记录")
    print(f"{'='*60}")

    if not dry_run and not args.yes:
        response = input("\n确认继续? (y/N): ")
        if response.lower() != "y":
            print("已取消")
            return 0

    moved_gids = []
    failed_titles = []

    for manga in to_process:
        success = manager.move_manga_to_pc(
            manga,
            dest_dir,
            remove_from_phone=args.remove and not dry_run,
            dry_run=dry_run,
        )
        if success:
            moved_gids.append(manga["gid"])
        else:
            failed_titles.append(manga["title"])

    print(f"\n{'='*60}")
    print(f"完成: 成功 {len(moved_gids)}/{len(to_process)}")
    if failed_titles:
        print("失败列表:")
        for t in failed_titles:
            print(f"  - {t}")

    if dry_run:
        print("\n[DRY-RUN] 以上为预演结果, 未进行实际操作。")
        print("确认无误后去掉 --dry-run 参数执行实际迁移。")
        return 0

    if args.sync_db and moved_gids:
        deleted_count = manager.clean_database_records(moved_gids)
        print(f"\n已清理 {deleted_count}/{len(moved_gids)} 条数据库记录")

        if manager.create_backup_and_push():
            print(f"\n完成! 数据库已更新并推送到手机")
            print(f"本地备份: {manager.backup_db_path}")
        else:
            print("\n警告: 数据库推送失败, 本地备份已保存")

    return 0


def cmd_clean(manager: MangaManager, args) -> int:
    if args.gids:
        gids_to_clean = args.gids
        print(f"\n将清理 {len(gids_to_clean)} 个指定GID: {', '.join(map(str, gids_to_clean))}")
    else:
        print("未指定GID, 将自动检测不存在的漫画...")
        missing_manga = manager.find_missing_manga()

        if not missing_manga:
            print("\n所有数据库记录的漫画文件均存在, 无需清理。")
            return 0

        gids_to_clean = [m["gid"] for m in missing_manga]

        print(f"\n{'='*60}")
        print(f"找到 {len(missing_manga)} 个不存在的漫画:")
        print(f"{'='*60}\n")
        for manga in missing_manga:
            print(f"  标题: {manga['title']}")
            print(f"  GID:  {manga['gid']}")
            print(f"  目录: {manga['dirname']}")
            print(f"  状态: {manga['state_text']}")
            print()

    if not args.auto:
        response = input(f"确认清理 {len(gids_to_clean)} 条记录? (y/N): ")
        if response.lower() != "y":
            print("已取消")
            return 0

    deleted_count = manager.clean_database_records(gids_to_clean)
    print(f"\n已清理 {deleted_count}/{len(gids_to_clean)} 条记录")

    if args.push:
        if manager.create_backup_and_push():
            print(f"\n完成! 数据库已更新并推送到手机")
            print(f"本地备份: {manager.backup_db_path}")
        else:
            print("\n警告: 数据库推送失败, 本地备份已保存")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EhViewer漫画管理工具 - 将已读漫画从手机迁移到电脑",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
推荐工作流:
  1. 手机EhViewer导出数据库: 设置 → 高级 → 导出数据
  2. 预检文件名问题:   python main.py check-names
  3. 小批量预演:        python main.py move --dest D:/Manga --batch-size 3 --dry-run
  4. 小批量实际移动:   python main.py move --dest D:/Manga --batch-size 3 --remove --sync-db
  5. 确认无误后全量:   python main.py move --dest D:/Manga --remove --sync-db
  6. 手机导入数据库:   设置 → 高级 → 导入数据 → 选择推送的文件
        """,
    )

    sub = parser.add_subparsers(dest="command", help="子命令")

    # analyze
    p_analyze = sub.add_parser("analyze", help="分析漫画阅读进度")
    p_analyze.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"阅读进度阈值 (默认: {DEFAULT_THRESHOLD})",
    )

    # check-names
    sub.add_parser("check-names", help="预检目录名的Windows文件名兼容性 (无需连接手机)")

    # stats
    sub.add_parser("stats", help="查看数据库统计信息")

    # move
    p_move = sub.add_parser("move", help="移动已读漫画到电脑")
    p_move.add_argument("--dest", required=True, help="目标目录路径")
    p_move.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"阅读进度阈值 (默认: {DEFAULT_THRESHOLD})",
    )
    p_move.add_argument(
        "--remove", action="store_true",
        help="成功移动后从手机删除原文件",
    )
    p_move.add_argument(
        "--sync-db", action="store_true",
        help="同步更新EhViewer数据库 (删除已移动漫画的记录并推送回手机)",
    )
    p_move.add_argument(
        "--batch-size", type=int, default=0, metavar="N",
        help="每次最多移动N个漫画 (0=全部); 建议首次测试用 --batch-size 3",
    )
    p_move.add_argument(
        "--dry-run", action="store_true",
        help="仅预演, 打印操作计划但不执行实际操作",
    )
    p_move.add_argument(
        "--yes", "-y", action="store_true",
        help="跳过确认提示直接执行 (用于脚本/管道调用)",
    )

    # clean
    p_clean = sub.add_parser("clean", help="清理数据库中不存在漫画的记录")
    p_clean.add_argument(
        "--gids", nargs="+", type=int,
        help="要清理的漫画GID列表 (不指定则自动检测不存在的漫画)",
    )
    p_clean.add_argument(
        "--push", action="store_true",
        help="清理后推送数据库到手机",
    )
    p_clean.add_argument(
        "--auto", action="store_true",
        help="跳过确认提示 (自动模式)",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # check-names 只需要数据库, 不需要检测手机目录
    # 但仍需初始化(拉取数据库)
    manager = MangaManager()
    try:
        if not manager.initialize():
            return 1

        dispatch = {
            "analyze": cmd_analyze,
            "check-names": cmd_check_names,
            "stats": cmd_stats,
            "move": cmd_move,
            "clean": cmd_clean,
        }

        handler = dispatch.get(args.command)
        if handler is None:
            parser.print_help()
            return 1

        return handler(manager, args)

    finally:
        manager.cleanup()


if __name__ == "__main__":
    sys.exit(main())
