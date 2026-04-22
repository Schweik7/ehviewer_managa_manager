# EhViewer 漫画管理工具

将 Android 手机上 EhViewer 已读漫画批量迁移到电脑，并同步清理 EhViewer 数据库记录。
支持 GUI 图形界面和命令行两种使用方式。

## 特性

- **GUI 图形界面**：直接运行 `python main.py` 即进入可视化操作界面（需 PyQt5）
- **Windows 文件名兼容**：自动净化含 `: * ? " < > | \ /` 等非法字符的目录名，生成映射记录
- **adb 非 ASCII 路径修复**：通过临时目录绕过 adb 在 Windows 上无法处理中文路径的限制
- **阅读进度过滤**：读取 `.ehviewer` 文件中的进度信息，按阈值筛选已读漫画
- **数据库同步**：迁移完成后自动删除对应数据库记录并推送回手机
- **小批量测试**：通过 `--batch-size` 和 `--dry-run` 先验证再全量迁移

## 依赖

- Python ≥ 3.10
- PyQt5（仅 GUI 模式需要，命令行模式不需要）
- Android SDK Platform-Tools（`adb` 命令需在 PATH 中）
- 手机开启 USB 调试 / 无线调试

```bash
pip install PyQt5        # 安装 GUI 依赖（可选）
```

## 快速开始

### 1. 准备

```bash
# 克隆仓库
git clone <repo-url>
cd ehviewer_manga_manager

# 验证 adb 可用
adb devices

# 在手机 EhViewer 中导出数据库
# 设置 → 高级 → 导出数据
```

### 2. GUI 模式（推荐）

```bash
python main.py   # 无参数启动 GUI
```

GUI 启动后自动连接设备并拉取数据库，操作流程：

1. 顶部状态栏确认设备已连接
2. **移动漫画** 选项卡 → 选择目标目录 → 点击「① 分析」
3. 分析完成后点击「② 预演」预览，确认无误后点击「③ 执行移动」
4. 勾选「同步更新 EhViewer 数据库」可自动推送更新后的 DB 回手机

### 3. 命令行工作流

```bash
# 第一步：预检文件名问题（扫描哪些目录名需要净化）
python main.py check-names

# 第二步：小批量预演（不执行实际操作）
python main.py move --dest D:/Manga --batch-size 3 --dry-run

# 第三步：小批量实际执行（先测试3个）
python main.py move --dest D:/Manga --batch-size 3 --remove --sync-db

# 第四步：确认无误后全量迁移
python main.py move --dest D:/Manga --remove --sync-db

# 第五步：在手机 EhViewer 中导入更新后的数据库
# 设置 → 高级 → 导入数据 → 选择 ehviewer_cleaned_*.db
```

## 命令参考

### `analyze` — 分析阅读进度

```bash
python main.py analyze [--threshold 0.9]
```

### `check-names` — 预检文件名兼容性

扫描数据库中所有目录名，列出在 Windows 上需要净化的条目及净化后的名称（不需要扫描手机文件）。

### `move` — 移动漫画

```bash
python main.py move --dest <目标目录> [选项]
```

| 参数 | 说明 |
|------|------|
| `--dest` | 本地目标目录（必填） |
| `--threshold` | 阅读进度阈值，默认 0.9（90%） |
| `--batch-size N` | 本次最多移动 N 个，0 表示全部 |
| `--dry-run` | 仅预演，不执行实际操作 |
| `--yes / -y` | 跳过确认提示（用于脚本/管道） |
| `--remove` | 成功移动后从手机删除原文件 |
| `--sync-db` | 清理已移动漫画的数据库记录并推送到手机 |

### `stats` — 数据库统计

### `clean` — 清理数据库记录

```bash
python main.py clean --push          # 自动检测并清理
python main.py clean --gids 123 456  # 手动指定 GID
python main.py clean --auto --push   # 跳过确认
```

## 文件名净化说明

EhViewer 下载的漫画目录名来自画廊标题，常含 Windows 非法字符：

| 原始字符 | Windows 规则 | 处理方式 |
|----------|-------------|---------|
| `: * ? " < > \| \ /` | 文件名非法字符 | 替换为 `_` |
| 控制字符 (0x00–0x1F) | 不可用 | 替换为 `_` |
| 首尾空格/`.` | Windows 不允许 | 去除 |
| `CON PRN AUX NUL COM* LPT*` | 保留设备名 | 前缀 `_` |
| 超长名称 (>240 字符) | NTFS 单组件 ≤255 字符 | 截断 |

净化后的映射关系保存在目标目录的 `name_mapping.txt` 中。

## 项目结构

```
.
├── main.py                  # 入口 (无参数→GUI, 有参数→CLI)
├── ehviewer/
│   ├── __init__.py
│   ├── config.py            # 常量配置
│   ├── filename_utils.py    # Windows文件名净化
│   ├── spider_info.py       # .ehviewer进度文件解析
│   ├── adb_manager.py       # ADB操作封装
│   ├── database.py          # SQLite数据库操作
│   ├── manager.py           # 主业务逻辑
│   └── gui.py               # PyQt5 图形界面
├── archive/                 # 原始v3脚本归档
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 开发过程中遇到的问题及解决方案

### 问题 1：文件名截断过于激进（中文名被大量截断）

**现象**：`check-names` 发现 26 个目录名需要净化，但实际上大多数只是名字较长。

**根因**：初版用 UTF-8 字节数（`len(name.encode("utf-8"))`）限制长度，设为 200 字节。
但 NTFS 的实际限制是 **255 个 Unicode 字符**（UTF-16 单元），不是字节数。
一个中文字符 UTF-8 占 3 字节，却只占 1 个 NTFS 字符槽位，导致限制比实际值严格 3 倍。

**解决**：改用 `len(name)` Python 字符数限制到 240，符合 NTFS 实际限制。
修复后问题从 26 个降为 1 个（那个确实以 `...` 结尾，Windows 不允许文件名末尾有点号）。

---

### 问题 2：Windows 终端中文乱码

**现象**：运行 `check-names` 时，中文标题全部显示为 `?` 乱码。

**根因**：Windows 中文系统默认代码页是 GBK（CP936），Python 的 `sys.stdout` 默认使用系统代码页，
无法正确输出 UTF-8 编码的中文字符。

**解决**：在 `main.py` 顶部添加：
```python
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
```

---

### 问题 3：subprocess 解析 adb 输出时报 GBK 解码错误

**现象**：运行 `move --dry-run` 时大量线程报 `UnicodeDecodeError: 'gbk' codec can't decode byte`。

**根因**：`subprocess.run(..., text=True)` 在 Windows 中文系统上默认使用 GBK 编码解码子进程输出，
而 `adb` 的输出是 UTF-8（包含中文漫画名），两者编码不匹配。

**解决**：为所有 `subprocess.run` 调用显式指定编码：
```python
_SUBPROCESS_KWARGS = dict(capture_output=True, text=True, encoding="utf-8", errors="replace")
```

---

### 问题 4：adb pull 无法处理目标路径中的非 ASCII 字符

**现象**：实际执行移动时，3 个漫画全部失败，错误为：
```
adb: error: failed to create directory '...[宇宙田协]': Illegal byte sequence
adb: error: cannot create '...\[Ng] 催眠妈妈1\..\.ehviewer': Not a directory
```

**根因**：adb 在 Windows 上通过 ANSI API 处理路径，无法处理目标路径中的非 ASCII 字符（中文、日文等）。
由于 EhViewer 的漫画目录名几乎都包含中文（来自汉化组标签、中文标题），
直接用原始 dirname 作为 `adb pull` 目标必然失败。

**解决**：`pull_manga` 改为**两步策略**：
1. 先拉到 GID 命名的 ASCII 临时目录（如 `_pull_tmp_3759443`），adb 可以正常处理
2. 用 `os.rename()` 将临时目录重命名为最终 Unicode 目标路径

NTFS 原生支持 Unicode 文件名，`os.rename` 不受 adb 的 ANSI API 限制，两步合一解决问题。

---

### 问题 5：`echo y | python main.py move` 管道在 Windows Git Bash 中失效

**现象**：用管道传递 `y` 给 `input()` 确认提示，脚本退出码为 1，漫画未被移动。

**根因**：在 Windows Git Bash 中，通过管道给 Python 的 `input()` 传输数据时行为不一致，
`y` 未能被 `input()` 读到，导致确认失败退出。

**解决**：新增 `--yes / -y` 参数，跳过交互式确认，适合脚本/自动化场景：
```bash
python main.py move --dest D:/Manga --batch-size 3 --yes
```
