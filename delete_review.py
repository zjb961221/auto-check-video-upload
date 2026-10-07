"""A second count confirmation for consequential bulk deletes."""
from collections import Counter
from tkinter import messagebox,simpledialog
from updates import is_delete


def confirm_large_delete(parent,preview,threshold=20):
    if not is_delete(preview.operation) or len(preview.rows)<threshold:return True
    count=len(preview.rows)
    return simpledialog.askinteger('再次确认批量删除',f'即将永久删除 {count} 条完整记录。请输入数字 {count} 再次确认。',parent=parent,minvalue=0)==count


def delete_summary(preview):
    if not is_delete(preview.operation):return ''
    compiled=preview.operation['compiled']
    conditions=' AND '.join(f'{c} {op} {preview.params[p]!r}' for c,op,p in compiled['conditions'])
    groups=''
    if 'device_id' in preview.columns:
        counts=Counter(str(row[preview.columns.index('device_id')]) for row in preview.rows)
        groups='\n设备分布：'+', '.join(f'{k}: {v} 条' for k,v in list(counts.items())[:20])
        if len(counts)>20:groups+=f'（共 {len(counts)} 台设备）'
    return f'表：{compiled["table"]}\n筛选：{conditions}\n匹配：{len(preview.rows)} 条，上限：{preview.operation["max_rows"]} 条'+groups
