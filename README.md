# 视频上传检查 · Windows 数据库固定查询工具

中文 Windows 桌面应用，使用 Python 3.12、Tkinter 和 PyMySQL。客户无需填写 SQL，可选择预配置查询并导出结果。

**当前是 v0.2.0 可配置查询工具，不包含真实的视频上传判断逻辑。仓库中的两条查询是明确标注的示例。上线前需要提供实际 SQL、字段含义与成功/失败判定规则。**

## 客户使用

1. 下载 GitHub Actions 最新成功构建的 `VideoUploadCheck-Windows-x64` 压缩包，完整解压。
2. 保持 `VideoUploadCheck.exe` 和 `queries.json` 在同一目录，双击 EXE。无需安装 Python。
3. 填写 MySQL 地址、端口、数据库、用户名和密码，点击“测试连接”。
4. 选择固定查询，填写所需参数，点击“执行查询”。结果为空会显示 0 行，不代表故障。
5. 可导出当前结果为带 UTF-8 BOM 的 CSV，便于 Excel 打开。以公式字符开头的文本会添加单引号以防公式执行。

默认勾选“记住密码（本机加密保存）”。点击“保存连接信息”可立即保存；连接测试或查询成功后自动保存本次使用的连接；下次启动自动恢复地址、端口、数据库、用户名和密码，无需再次输入。手动保存不代表连接验证成功。自动保存失败只提示警告，不影响本次查询结果。

密码通过 Windows DPAPI 按当前用户加密，配置位于 `%LOCALAPPDATA%\VideoUploadCheck\connection.json`，不写入明文密码。通常需在原电脑、原 Windows 用户下读取；更换电脑或用户后请重新输入。取消“记住密码”并点击保存，会移除之前保存的密码。旧版不含密码的配置可自动兼容。升级 EXE 不会清除已保存配置。

非 Windows 开发环境首次启动默认不勾选“记住密码”；不提供明文保存降级。

如启用证书验证，在 CA 证书路径中填写 PEM 文件，连接地址必须匹配服务端证书。未提供 CA 时不保证连接加密，请使用可信内网或 VPN；需要强制 TLS 的环境务必提供 CA。

## 实施人员配置固定 SQL

编辑 EXE 同目录的 `queries.json`，点击“重新加载查询”生效；配置错误时保留上次有效配置。界面不提供任意 SQL 编辑器，但本地配置文件可被修改，因此**数据库只读账号是权限边界**，不可分发管理员账号。

格式示例（表和字段是假设，必须替换后再交付）：

```json
[
  {
    "name": "指定时段视频记录",
    "description": "按创建时间查询；时间格式：2026-09-28 00:00:00",
    "sql": "SELECT id, create_date, upload_status FROM video_upload WHERE create_date >= %(start_time)s AND create_date < %(end_time)s ORDER BY create_date DESC",
    "params": [
      {"name": "start_time", "label": "开始时间", "type": "datetime"},
      {"name": "end_time", "label": "结束时间", "type": "datetime"}
    ],
    "date_ranges": [["start_time", "end_time"]]
  }
]
```

- 仅接受以 SELECT 开始的单条查询，可带一个末尾分号。不允许 SQL 注释、INTO、变量赋值或加锁语句；不支持 CTE、存储过程和多语句。校验是受限语法检查，不是完整 SQL 解析器或权限防火墙。
- 字面量中的反斜杠转义不支持，以避免 sql_mode 歧义；含反斜杠的值请通过参数绑定传入。
- 参数类型支持 `text`、`integer`、`datetime`，支持中文 `label` 和 `default`，最多 8 个。旧版字符串参数列表仍然可用，按 text 处理。`date_ranges` 指定需要校验先后顺序的日期参数对。
- 参数使用 `%(name)s`，在 params 中声明；不要自行加引号或拼接客户输入。
- 有绑定参数的 SQL 中，字面的百分号按驱动规则写成 `%%`，如 `LIKE '%%video%%'`；无参数 SQL 使用普通 `%`。
- 保留原 SELECT 和 ORDER BY，只追加或收紧最外层 LIMIT，最多取 2,001 行，用额外一行判断截断；展示和导出前 2,000 行。支持数字形式 `LIMIT n`、`LIMIT offset, n`、`LIMIT n OFFSET offset`；不支持 LIMIT 参数。
- 支持重名输出列，但建议使用清晰别名。无 ORDER BY 的 SQL 不保证顺序；稳定分页应使用包含唯一键的排序。
- 行数限制不等于字节数限制，避免 SELECT * 查询大型 BLOB 或视频内容；应选择需要展示的业务字段。
- 建议使用 MySQL 5.7.8+ / 8.0；不承诺 MariaDB 兼容。使用只读事务，设置 30 秒 SELECT 执行时间限制及 35 秒读取超时。服务端时间限制有适用范围，不代替 DBA 的资源管控。
- 客户账号仅授予所需表的 SELECT 权限，限制来源 IP，不授予 FILE、EXECUTE 或写权限。
- 首次验证请对照运维人员在数据库中手工执行的查询结果，特别核对时间字段、时区、状态码及“已上传”的业务含义。

## 客户操作与升级

- 查询期间显示进度及等待秒数，锁定连接和参数输入，避免输入与结果不一致。
- 连接测试不清空查询结果；查询失败保留上次结果并明确提示。结果上方标记查询名、主机、数据库、生成时间与截断状态。
- 成功且无匹配数据明确显示 0 行；不把它直接判定为业务故障。
- 屏幕单元格最多展示 1,000 字符，CSV 保留完整字段。CSV 采用临时文件原子替换，写入失败不破坏已有文件。
- 日志位于 `%LOCALAPPDATA%\VideoUploadCheck\app.log`，仅记录时间、事件、耗时、错误类型和代码；不记录密码、SQL、参数或原始异常文本。客户可以提供错误代码和诊断编号协助排查。
- 升级时关闭程序并替换 EXE，**保留已定制的 queries.json**；连接配置位于用户目录，不会随 EXE 替换清除。
- 连接测试通过只说明基础连接可用；执行实际业务查询才验证相应的表、字段和权限。程序关闭需等待当前操作返回；不提供强制取消数据库查询功能。

## 代码结构

| 文件 | 责任 |
|---|---|
| `app.py` | 界面、后台任务调度与结果状态 |
| `queries.py` | 查询配置校验、参数转换和行数限制 |
| `database.py` | MySQL 连接、只读事务和连接测试 |
| `settings.py` | 本地配置及 Windows DPAPI 加密 |
| `diagnostics.py` | 版本号、脱敏日志和错误提示 |
| `core.py` | CSV 导出及旧模块入口兼容 |

保持单进程桌面应用，后台线程只负责数据库请求，所有 Tk 更新在主线程执行。不为小型工具引入 Web 服务或额外部署组件。

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

测试覆盖参数绑定、日期范围、SQL 排序与 LIMIT、失败清理、配置迁移、加密失败恢复、结果保留、CSV 防护和日志脱敏。运行：

```powershell
python -m unittest discover -s tests -v
```

Windows 构建任务还会执行真实 Tk 窗口测试和 DPAPI 跨进程恢复测试。独立 MySQL 8.0 工作流使用临时数据库检查排序、截断、重复列名、参数绑定和错误后重连。测试凭据仅用于 CI 临时容器，不连接任何客户数据库。

本地无 Windows 桌面或 MySQL 时会明确跳过对应测试；提交后以 Actions 结果为准。真实业务 SQL 正确性、现场网络和 EXE 用户验收仍需在客户环境核对。
