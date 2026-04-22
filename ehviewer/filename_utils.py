"""
Windows文件名安全处理工具

Windows非法字符: \\ / : * ? " < > |
Windows保留名称: CON, PRN, AUX, NUL, COM0-9, LPT0-9
其他限制: 不能以空格或点结尾, NTFS单组件最长255字节
"""

import re
import platform

# Windows文件名非法字符集
_WINDOWS_ILLEGAL_CHARS = r'\/:*?"<>|'
# 控制字符 (ASCII 0-31)
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')

# Windows保留设备名 (不区分大小写, 带或不带扩展名均不可用)
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM0", "COM1", "COM2", "COM3", "COM4",
    "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT0", "LPT1", "LPT2", "LPT3", "LPT4",
    "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

# 保守的单文件名最大长度 (NTFS上限255, 留出余量)
MAX_FILENAME_LEN = 200


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """
    将任意字符串转为在Windows上合法的文件名组件。

    - 替换所有非法字符为 replacement
    - 去除首尾的空格和点
    - 处理Windows保留名称
    - 截断超长名称(保留末尾以保留GID等关键信息)
    - 在非Windows系统上也执行同样的净化(确保跨平台一致)

    返回净化后的字符串。若原始名称已安全, 返回原始名称(不做不必要修改)。
    """
    if not name:
        return "_empty_"

    result = name

    # 替换非法字符
    for ch in _WINDOWS_ILLEGAL_CHARS:
        result = result.replace(ch, replacement)

    # 替换控制字符
    result = _CONTROL_CHARS_RE.sub(replacement, result)

    # 去除首尾空格和点 (Windows不允许)
    result = result.strip(" .")

    if not result:
        return "_sanitized_"

    # 处理保留名称 (检查去掉扩展名后的部分)
    stem = result.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        result = replacement + result

    # 截断超长文件名 (优先保留尾部, 因为EhViewer目录名通常以GID开头)
    if len(result.encode("utf-8")) > MAX_FILENAME_LEN:
        # 按字符截断, 同时确保UTF-8字节数不超限
        while len(result.encode("utf-8")) > MAX_FILENAME_LEN and result:
            result = result[:-1]
        result = result.rstrip(" .")

    return result or "_truncated_"


def needs_sanitization(name: str) -> bool:
    """检查文件名是否需要净化(用于日志提示)。"""
    return sanitize_filename(name) != name


def make_name_mapping_note(original: str, sanitized: str) -> str:
    """生成原始名称到净化名称的备注字符串(用于日志)。"""
    return f"[重命名] {original!r} -> {sanitized!r}"
