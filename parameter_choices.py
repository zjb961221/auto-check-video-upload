"""Validated label/value choices; SQL always receives the configured value."""

def normalize_options(spec):
    if 'options' not in spec:
        return
    options = spec['options']
    if not isinstance(options, list) or not 1 <= len(options) <= 200:
        raise ValueError(f"{spec['label']}：options 必须包含 1–200 个选项")
    labels, values, normalized = set(), set(), []
    for item in options:
        if not isinstance(item, dict) or set(item) != {'label', 'value'}:
            raise ValueError(f"{spec['label']}：每个选项必须包含 label 和 value")
        label, value = item['label'], item['value']
        if not isinstance(label, str) or not label.strip() or label in labels:
            raise ValueError(f"{spec['label']}：选项显示名称不能为空或重复")
        if type(value) not in (str, int) or (spec['type'] != 'integer' and not isinstance(value, str)):
            raise ValueError(f"{spec['label']}：选项值类型与参数不匹配")
        value = str(value)
        if not value.strip() or len(value) > 4096:
            raise ValueError(f"{spec['label']}：选项值必须为非空文本，最多 4096 字符")
        if spec['type'] == 'integer':
            try:
                value = str(int(value))
            except ValueError:
                raise ValueError(f"{spec['label']}：选项值必须为整数") from None
        elif spec['type'] == 'datetime':
            from datetime import datetime
            try:
                datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                raise ValueError(f"{spec['label']}：选项值必须为有效日期时间") from None
        if value in values:
            raise ValueError(f"{spec['label']}：选项值不能重复")
        labels.add(label); values.add(value)
        normalized.append({'label': label, 'value': value})
    default = str(spec.get('default', ''))
    if spec['type'] == 'integer' and default:
        try:
            default = str(int(default))
        except ValueError:
            raise ValueError(f"{spec['label']}：默认值必须为整数") from None
    if default and default not in values:
        raise ValueError(f"{spec['label']}：default 必须是 options 中的 value，不能填写显示名称")
    spec['options'], spec['default'] = normalized, default


def validate_choice(spec, value):
    if 'options' in spec and (not isinstance(value, str) or value not in {item['value'] for item in spec['options']}):
        raise ValueError(f"请从“{spec['label']}”下拉框中选择有效选项")
