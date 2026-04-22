"""解析 .ehviewer 文件中的阅读进度信息。"""


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
        self.ptoken_map: dict = {}

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
            with open(self.file_path, "r", encoding="ascii") as f:
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
            idx += 1  # skip thumbnail URL count placeholder
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
                if " " in line:
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        page_idx = int(parts[0])
                        ptoken = parts[1]
                        if ptoken and ptoken != "failed":
                            self.ptoken_map[page_idx] = ptoken

            return self.gid != -1 and bool(self.token) and self.pages > 0

        except (IOError, ValueError, IndexError) as e:
            print(f"读取 {self.file_path} 失败: {e}")
            return False

    def get_read_progress(self) -> float:
        if self.pages <= 0:
            return 0.0
        return (self.start_page + 1) / self.pages
