# EhViewer 漫画管理工具

将 Android 手机上 EhViewer 已读漫画批量迁移到电脑，并同步清理 EhViewer 数据库记录。

## 特性

- **Windows 文件名兼容**：自动净化含 `: * ? " < > | \ /` 等非法字符的目录名，生成映射记录
- **阅读进度过滤**：读取 `.ehviewer` 文件中的进度信息，按阈值筛选已读漫画
- **数据库同步**：迁移完成后自动删除对应数据库记录并推送回手机
- **小批量测试**：通过 `--batch-size` 和 `--dry-run` 先验证再全量迁移

## 依赖

- Python ≥ 3.10
- Android SDK Platform-Tools（`adb` 命令需在 PATH 中）
- 手机开启 USB 调试 / 无线调试

## 快速开始

### 1. 准备

```bash
# 克隆仓库
git clone <repo-url>
cd ehviewer_manga_manager

# 验证 adb 可用
adb devices
```

在手机 EhViewer 中导出数据库：**设置 → 高级 → 导出数据**

### 2. 推荐工作流

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

列出手机上阅读进度达到阈值的漫画，不做任何移动操作。

### `check-names` — 预检文件名兼容性

```bash
python main.py check-names
```

扫描数据库中所有目录名，列出在 Windows 上需要净化的条目及净化后的名称。**不需要连接手机扫描文件**（直接读数据库）。

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
| `--remove` | 成功移动后从手机删除原文件 |
| `--sync-db` | 清理已移动漫画的数据库记录并推送到手机 |

### `stats` — 数据库统计

```bash
python main.py stats
```

### `clean` — 清理数据库记录

```bash
# 自动检测手机上不存在的漫画并清理记录
python main.py clean --push

# 手动指定 GID
python main.py clean --gids 123456 789012 --push

# 跳过确认（自动模式）
python main.py clean --auto --push
```

## 文件名净化说明

EhViewer 下载的漫画目录名来自画廊标题，常含 Windows 非法字符：

| 原始字符 | Windows 规则 | 处理方式 |
|----------|-------------|---------|
| `: * ? " < > \| \ /` | 文件名非法字符 | 替换为 `_` |
| 控制字符 (0x00–0x1F) | 不可用 | 替换为 `_` |
| 首尾空格/`.` | 不可见/歧义 | 去除 |
| `CON PRN AUX NUL COM* LPT*` | 保留设备名 | 前缀 `_` |
| 超长名称 (>200 字符) | NTFS 单组件 ≤255 字节 | 截断 |

净化后的映射关系保存在目标目录的 `name_mapping.txt` 中：

```
净化后名称    <--    原始名称
```

## 项目结构

```
.
├── main.py                  # 入口，命令行解析
├── ehviewer/
│   ├── __init__.py
│   ├── config.py            # 常量配置
│   ├── filename_utils.py    # Windows文件名净化
│   ├── spider_info.py       # .ehviewer进度文件解析
│   ├── adb_manager.py       # ADB操作封装
│   ├── database.py          # SQLite数据库操作
│   └── manager.py           # 主业务逻辑
├── requirements.txt
├── .gitignore
└── README.md
```
