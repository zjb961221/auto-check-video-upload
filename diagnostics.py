"""Safe support messages: never log exception text, SQL, params or credentials."""
import logging
from logging.handlers import RotatingFileHandler
from uuid import uuid4

VERSION = '0.8.0'
MESSAGES = {
    1062: '更新值违反唯一约束，事务未提交，请核对数据。',
    1205: '等待记录锁超时，请稍后重新预览。',
    1213: '数据库发生死锁，请重新预览后再提交。',
    1406: '写入内容超过字段长度，请缩短后重新预览。',
    1366: '写入值与字段类型不匹配，请核对输入。',
    1045: '账号或密码错误，或该账号没有远程连接权限。',
    1049: '数据库不存在，请检查数据库名称。',
    1146: '查询表不存在，请联系实施人员核对 SQL。',
    1054: '查询字段不存在，请联系实施人员核对 SQL。',
    1142: '账号没有查询权限，请联系数据库管理员。',
    1064: 'SQL 语法错误，请联系实施人员核对 SQL。',
    1193: '数据库不支持所需查询超时设置，请核对 MySQL 版本。',
    1792: '查询尝试执行只读事务禁止的操作，请联系实施人员。',
    2003: '无法连接，请检查地址、端口、网络及防火墙。',
    2013: '连接中断或读取超时，请缩小查询范围并检查网络。',
    2026: 'TLS 连接失败，请检查 CA 证书和数据库地址是否匹配。',
    3024: '查询超过 30 秒，请缩小时间范围。',
}


def configure_logging(folder):
    logger = logging.getLogger('video_check')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(folder / 'app.log', maxBytes=512_000, backupCount=2, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


def error_message(exc, logger):
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    reference = uuid4().hex[:8]
    logger.error('event=operation_failed ref=%s type=%s code=%s', reference, type(exc).__name__, code)
    text = MESSAGES.get(code, '操作失败，请检查网络、证书、数据库版本及查询配置。')
    return f'{text}\n错误代码：{code or "无"} · 诊断编号：{reference}'


def configuration_error(path, exc):
    """Actionable file errors, without excerpts that could contain credentials."""
    import json
    from pathlib import Path
    name = Path(path).name
    if isinstance(exc, FileNotFoundError):
        reason = '文件不存在，请将该配置文件放在 EXE 同目录'
    elif isinstance(exc, PermissionError):
        reason = '没有读取权限，请检查文件权限或移到可读目录'
    elif isinstance(exc, json.JSONDecodeError):
        reason = f'JSON 格式错误：第 {exc.lineno} 行、第 {exc.colno} 列；请检查逗号、引号和括号'
    elif isinstance(exc, UnicodeError):
        reason = '文件编码无法读取，请另存为 UTF-8'
    elif isinstance(exc, (ValueError, TypeError)):
        reason = str(exc) if isinstance(exc, ValueError) else '字段类型不正确，请核对配置说明'
    else:
        reason = '文件无法读取，请检查文件状态和权限'
    return f'{name}：{reason}。修正后点击重新加载。'
