# 视频上传检查 · Windows 数据库固定查询工具

中文 Windows 桌面应用，使用 Python 3.12、Tkinter 和 PyMySQL。客户无需填写 SQL，可选择预配置查询并导出结果。

**当前是可配置的查询工具第一版，不包含真实的视频上传判断逻辑。仓库中的两条查询是明确标注的示例。上线前需要提供实际 SQL、字段含义与成功/失败判定规则。**

## 客户使用

1. 下载 GitHub Actions 最新成功构建的 `VideoUploadCheck-Windows-x64` 压缩包，完整解压。
2. 保持 `VideoUploadCheck.exe` 和 `queries.json` 在同一目录，双击 EXE。无需安装 Python。
3. 填写 MySQL 地址、端口、数据库、用户名和密码，点击“测试连接”。
4. 选择固定查询，填写所需参数，点击“执行查询”。结果为空会显示 0 行，不代表故障。
5. 可导出当前结果为带 UTF-8 BOM 的 CSV，便于 Excel 打开。以公式字符开头的文本会添加单引号以防公式执行。

默认勾选“记住密码（本机加密保存）”。点击“保存连接信息”、测试连接或执行查询时，保存当前输入；下次启动自动恢复地址、端口、数据库、用户名和密码，无需再次输入。保存不代表连接验证成功。

密码通过 Windows DPAPI 按当前用户加密，配置位于 `%LOCALAPPDATA%\VideoUploadCheck\connection.json`，不写入明文密码。通常需在原电脑、原 Windows 用户下读取；更换电脑或用户后请重新输入。取消“记住密码”并点击保存，会移除之前保存的密码。旧版不含密码的配置可自动兼容。升级 EXE 不会清除已保存配置。

开发环境非 Windows 时需取消“记住密码”；不提供明文保存降级。

如启用证书验证，在 CA 证书路径中填写 PEM 文件，连接地址必须匹配服务端证书。未提供 CA 时不保证连接加密，请使用可信内网或 VPN；需要强制 TLS 的环境务必提供 CA。

## 实施人员配置固定 SQL

编辑 EXE 同目录的 `queries.json`，重启应用生效。界面不提供任意 SQL 编辑器，但本地配置文件可被修改，因此**数据库只读账号是权限边界**，不可分发管理员账号。

格式示例（表和字段是假设，必须替换后再交付）：

```json
[
  {
    "name": "指定时段视频记录",
    "description": "按创建时间查询；时间格式：2026-09-28 00:00:00",
    "sql": "SELECT id, create_date, upload_status FROM video_upload WHERE create_date >= %(start_time)s AND create_date < %(end_time)s ORDER BY create_date DESC",
    "params": ["start_time", "end_time"]
  }
]
```

- 仅接受以 SELECT 开始的单条查询，不允许分号、注释、INTO 或加锁语句；不支持 CTE、存储过程和多语句。
- 参数使用 `%(name)s`，在 params 中声明；不要自行加引号或拼接客户输入。
- 使用 PyMySQL 参数时，SQL 中字面的百分号按驱动规则写成 `%%`，如 `LIKE '%%video%%'`。
- 查询被包装成派生表以强制限制最多取 2,001 行，展示和导出前 2,000 行，并显示截断提示。各输出列必须有唯一名称，重名字段请使用别名。
- 如结果顺序对业务至关重要，请在查询与目标 MySQL 版本上验证包装后的排序表现。
- 建议使用 MySQL 5.7.8+ / 8.0；不承诺 MariaDB 兼容。使用只读事务，设置 30 秒 SELECT 执行时间限制及 35 秒读取超时。服务端时间限制有适用范围，不代替 DBA 的资源管控。
- 客户账号仅授予所需表的 SELECT 权限，限制来源 IP，不授予 FILE、EXECUTE 或写权限。
- 首次验证请对照运维人员在数据库中手工执行的查询结果，特别核对时间字段、时区、状态码及“已上传”的业务含义。

## 开发运行

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Windows 打包

安装 Python 3.12（含 Tcl/Tk 与 Python Launcher），在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

只对本次脚本执行设置策略，不修改系统策略。打包输出在 `dist`，需分发 EXE 与 JSON。EXE 未做代码签名；如客户组织有签名要求，交付前按组织流程签名。

GitHub Actions 会在 main 推送、PR 或手动触发时执行测试和 Windows 打包，构建成功后在 Actions 页面下载产物。本项目不自动发布 GitHub Release。

## 验证状态

单元测试覆盖参数绑定、只读事务、结果截断、失败清理、非法配置拒绝、CSV 中文及公式防护。运行：

```powershell
python -m unittest discover -s tests -v
```

真实数据库连接、业务 SQL 正确性、Windows 界面显示与 EXE 启动需要在客户或验收环境完成验证；仅通过单元测试不能视作完成现场验收。
