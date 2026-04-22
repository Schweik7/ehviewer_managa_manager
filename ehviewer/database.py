"""EhViewer SQLite数据库操作封装。"""

import shutil
import sqlite3
from typing import Dict, List, Optional


class MangaDatabase:
    """漫画数据库管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> bool:
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            return True
        except sqlite3.Error as e:
            print(f"连接数据库失败: {e}")
            return False

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def backup(self, backup_path: str) -> bool:
        try:
            shutil.copy2(self.db_path, backup_path)
            print(f"数据库已备份到: {backup_path}")
            return True
        except Exception as e:
            print(f"备份数据库失败: {e}")
            return False

    def get_all_downloads(self) -> List[Dict]:
        if not self.conn:
            return []

        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT GID, TOKEN, TITLE, STATE, LEGACY, TIME, LABEL
                FROM DOWNLOADS
                ORDER BY TIME DESC
                """
            )
            return [
                {
                    "gid": row["GID"],
                    "token": row["TOKEN"],
                    "title": row["TITLE"],
                    "state": row["STATE"],
                    "legacy": row["LEGACY"],
                    "time": row["TIME"],
                    "label": row["LABEL"],
                }
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"查询下载记录失败: {e}")
            return []

    def get_download_dirname(self, gid: int) -> Optional[str]:
        if not self.conn:
            return None
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT DIRNAME FROM DOWNLOAD_DIRNAME WHERE GID = ?", (gid,)
            )
            row = cursor.fetchone()
            return row["DIRNAME"] if row else None
        except sqlite3.Error:
            return None

    def delete_download_by_gid(self, gid: int) -> bool:
        """删除指定GID的下载记录及相关数据。"""
        if not self.conn:
            return False

        try:
            cursor = self.conn.cursor()

            cursor.execute("DELETE FROM DOWNLOADS WHERE GID = ?", (gid,))
            deleted_downloads = cursor.rowcount

            cursor.execute("DELETE FROM DOWNLOAD_DIRNAME WHERE GID = ?", (gid,))

            try:
                cursor.execute("DELETE FROM GALLERY_TAGS WHERE GID = ?", (gid,))
            except sqlite3.Error:
                pass  # GALLERY_TAGS表可能不存在

            self.conn.commit()
            return deleted_downloads > 0

        except sqlite3.Error as e:
            print(f"  删除记录失败 (GID={gid}): {e}")
            self.conn.rollback()
            return False

    def get_statistics(self) -> Dict:
        if not self.conn:
            return {}

        try:
            cursor = self.conn.cursor()
            stats: Dict = {}

            cursor.execute("SELECT COUNT(*) as count FROM DOWNLOADS")
            stats["total_downloads"] = cursor.fetchone()["count"]

            cursor.execute(
                "SELECT STATE, COUNT(*) as count FROM DOWNLOADS GROUP BY STATE"
            )
            stats["by_state"] = {row["STATE"]: row["count"] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT COUNT(DISTINCT LABEL) as count FROM DOWNLOADS WHERE LABEL IS NOT NULL"
            )
            stats["total_labels"] = cursor.fetchone()["count"]

            return stats

        except sqlite3.Error as e:
            print(f"获取统计信息失败: {e}")
            return {}
