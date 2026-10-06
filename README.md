> v0.8.0：新增按主键 / 非空唯一键删除，支持完整记录预览、数量限制、确认删除及并发校验。参数可配置默认值，流程步骤可覆盖默认值。配置见 [DELETES.md](DELETES.md)；升级请保留现场配置。

> v0.7.0：正式更名为“客户自查运维工具”，EXE 为 ClientOpsTool.exe。SQL 查询参数新增可配置下拉框，显示名称与实际值分离，详见 [PARAMETERS.md](PARAMETERS.md)。为兼容已保存连接，用户配置仍沿用原 VideoUploadCheck 目录，无需迁移。

> v0.6.1 可靠性修复：前面步骤失效时仍可逐步返回处理；配置错误指出具体文件、JSON 行列或引用原因；网络/接口失败提示处理方向。结果显示异常会恢复界面并继续接收结果，写入结果无法确认时必须先核实服务端。小窗口优先保留操作区，避免大字体遮挡导航。

> v0.6.0 外观更新：默认曜石深色，可切换清爽浅色；右上角选择 85%–150% 字体缩放。Ctrl + 加号/减号调整，Ctrl+0 恢复 100%；F11 全屏，Esc 退出。外观偏好自动保存，窗口大小可拖动或最大化。小窗口自动用步骤下拉列表替代左侧栏，表单改成单列；高级工具支持双向滚动。缩放不会清空输入或改变流程状态。

> v0.5.0 新增默认“客户流程向导”：按步骤配置说明、查询、更新和 API。配置方法见 [WORKFLOWS.md](WORKFLOWS.md)。原独立操作移到“高级工具（实施人员）”。升级请保留现场 SQL/API 配置，并添加 workflows.json；示例 ref 名称须与现场操作名称一致。

# 客户自查运维工具 · Windows 桌面版

中文 Windows 桌面应用，使用 Python 3.12、Tkinter 和 PyMySQL。客户无需填写 SQL，可选择预配置查询并导出结果。

**当前是 v0.4.0 数据库与 API 工具，不包含真实的视频上传判断逻辑。仓库中的两条查询是明确标注的示例。上线前需要提供实际 SQL、字段含义与成功/失败判定规则。**

## 客户使用

1. 下载 GitHub Actions 最新成功构建的 `ClientOpsTool-Windows-x64` 压缩包，完整解压。
2. 保持 `ClientOpsTool.exe` 和 `queries.json` 在同一目录，双击 EXE。无需安装 Python。
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
| `updates.py` | 受限 UPDATE 校验、预览快照、并发检查和事务提交 |
| `updates_ui.py` | 独立更新窗口与确认流程 |
| `api_client.py` | HTTP 请求、鉴权、Cookie 会话与响应脱敏 |
| `api_config.py` | 接口模板与加密连接配置 |
| `api_ui.py` | 通用 API 工作窗口 |

保持单进程桌面应用，后台线程只负责数据库请求，所有 Tk 更新在主线程执行。不为小型工具引入 Web 服务或额外部署组件。

## 配置数据库 UPDATE（v0.3.0）

更新使用独立的 `updates.json`，不把 UPDATE 放进 `queries.json`。默认文件为 `[]`，不会启用任何示例更新。你自己编写 SQL，界面根据参数生成输入框。

1. 参考 EXE 同目录的 `updates.example.json`，将自己的操作数组写入 `updates.json`。
2. 在主窗口配置数据库连接，点击“数据库更新…”。
3. 选择操作、填写参数，点击“预览更新”，查看记录主键、字段、当前值和计划新值。
4. 核对目标主机、数据库、记录数，点击“确认并提交”，在确认框中确认。
5. 程序显示匹配行数和实际修改行数。同值更新可能实际修改 0 行。

```json
[
  {
    "name": "按通道编号修改视频编码",
    "description": "只修改指定 id 的 name 字段",
    "sql": "UPDATE `9video` SET `name`=%(new_name)s WHERE `id`=%(channel_id)s",
    "max_rows": 1,
    "params": [
      {"name": "channel_id", "label": "通道编号，例如 D1", "type": "text"},
      {"name": "new_name", "label": "新的视频编码", "type": "text"}
    ]
  }
]
```

### 支持范围

- 当前版本支持**单表、参数化赋值、AND 连接的条件**。SET 可含多个字段，每个新值必须用 `%(参数名)s`，占位符不加引号。
- WHERE 支持 `=`、`!=`、`<>`、`>`、`>=`、`<`、`<=`、`LIKE`，右侧必须为绑定参数。LIKE 通配符由输入值提供，如 `150622%`。
- 不支持任意 SQL：无 WHERE、JOIN、多表、OR、子查询、表达式赋值、SQL 注释、存储过程、INSERT、DELETE、DDL 都会拒绝。固定值也请使用带 `default` 的参数。用于 WHERE 定位的参数仍然必填。仅用于 SET 的参数旁新增输入方式：输入值、空字符串（text 参数）、数据库 NULL。SQL 和已有参数配置无需修改。选择特殊值后输入框禁用，其残留文字不会参与提交。
- NULL 会绑定为真正的数据库 NULL，预览前检查目标字段是否允许 NULL；不允许时直接拒绝。空字符串写入 `''`。普通输入框填写 `NULL` 仍是文本。整数和日期参数不提供空字符串选项。若同一参数同时出现在 SET 和 WHERE 中，不开放空值选项，需拆分成两个参数。
- 预览将三者区分显示为“数据库 NULL”、`""（空字符串）` 和 `"NULL"`。普通输入模式下留空仍提示必填，需明确选择空字符串或 NULL。
- 标识符支持英文、数字、下划线；数字开头的表名必须加反引号。不支持跨数据库表名或表别名。表及字段名采用数据库实际大小写。
- 目标必须是 **InnoDB 实体表且存在主键**，支持复合主键；禁止修改主键和生成列。没有主键时不会自动 ALTER TABLE，应由管理员确认合适的主键。
- `max_rows` 默认 1，可设为 1–100 的整数。超过上限会拒绝整个操作，不是悄悄只更新前几行。
- 数据库账号必须具备目标表的 SELECT 及所需字段的 UPDATE 权限，不能继续使用只有 SELECT 的账号。无需使用 root。

### 提交与并发语义

预览只读，不会提前执行 UPDATE 或长时间持锁。预览有效期 5 分钟；修改参数、重新加载配置或执行过一次提交后，必须重新预览。

提交使用独立事务，再次核对服务器标识、表结构与匹配记录，通过 FOR UPDATE 锁定记录并与预览比较。发现变化拒绝提交。实际写入逐条使用预览过的主键定位，不会因条件变化扩大写入范围。

所有目标行在一个事务中提交；任意执行错误、唯一约束冲突或数据转换警告会停止提交并回滚。等待行锁超时为 10 秒。若 COMMIT 阶段发生网络错误，数据库可能已经提交，程序会提示**结果无法确认，先查询核实，禁止直接重复提交**，不会自动重试。

影响行数是目标表直接更新的行数，不包含触发器或外键级联的副作用。实施前应由 DBA 核对触发器、级联规则与外部副作用；工具的事务保证不涵盖非事务表或外部系统。日志只记录成功计数或失败类型，不记录修改前后的业务值，不替代数据库审计日志。保持连接稳定，提交过程中不要结束进程。

升级时替换 EXE，并放入 `updates.example.json` 供参考；若已有自己的 `updates.json`，**不要被安装包中的空配置覆盖**。原有 `queries.json` 同样保留。重新打开更新窗口或点击“重新加载更新配置”可加载修改。

## 通用 HTTP API 调用（v0.4.0）

点击主窗口“API 调用…”，无需连接数据库。可以调用 WVP、任务调度平台或其他 HTTP API。平台路径和鉴权字段通过配置定义，而非固定在代码里。

### 操作流程

1. 选择接口示例，或选择“自定义接口”。
2. 在“服务与鉴权”填服务地址、鉴权方式和相关凭据。配置名称用于区分不同服务的已保存连接。
3. 如果使用“登录后 Token / Cookie”，先检查“登录请求配置”，点击“登录 / 获取会话”。Bearer、API Key、Basic 不需要额外登录。
4. 在“请求与参数”填写参数，检查或直接编辑请求 JSON。点击“预览请求”核对实际 URL、方法、Header 和请求体。
5. 点击“发送接口”，在“响应结果”查看 HTTP 状态、耗时和业务响应。非 GET/HEAD/OPTIONS 请求会要求确认；配置 `confirm: true` 可让 GET 操作也要求确认。

支持 GET、POST、PUT、PATCH、DELETE、HEAD、OPTIONS。请求体支持 JSON、URL 编码表单、raw 文本及无请求体；可自定义 Header 和查询参数。当前不提供 multipart 文件上传、流式订阅、OAuth 浏览器授权或自动生成 HMAC 签名。特殊鉴权可按接口文档补充签名 Header，但不能声称无需适配所有平台。

### 接口配置格式

EXE 同目录的 `api_requests.json` 为接口数组。编辑后点击“重新加载接口配置”。界面中的请求 JSON 编辑用于本次调用，不会自动写回接口配置文件。

```json
[
  {
    "name": "按设备编号调用接口",
    "description": "示例路径，必须替换成现场实际接口",
    "profile_name": "我的服务",
    "profile": {"auth_type": "bearer"},
    "request": {
      "method": "POST",
      "path": "/api/devices/{{device_id}}/check",
      "query": {},
      "headers": {},
      "body_type": "json",
      "body": {"enabled": "{{enabled}}"}
    },
    "params": [
      {"name": "device_id", "label": "设备编号", "type": "text"},
      {"name": "enabled", "label": "是否启用（true/false）", "type": "boolean", "default": "true"}
    ]
  }
]
```

`{{参数名}}` 为模板变量。JSON 中完整占位符会保留整数/布尔类型，字符串中的嵌入变量按文本替换，路径变量自动 URL 编码。参数支持 text、integer、boolean；最多 8 个。`required` 默认 true；敏感参数设置 `secret: true`，界面遮盖输入并在预览/响应中遮盖对应值。username、password、password_md5、token 是保留变量。

服务地址可以包含应用路径：例如 `http://主机:8080/xxl-job-admin`；接口 `path: "/login"` 会接在应用路径后，成为 `/xxl-job-admin/login`。path 不允许指向其他主机。服务地址不能嵌入用户名、密码、查询串或片段。

### 鉴权与会话

| 界面选项 | profile.auth_type | 填写方式 |
|---|---|---|
| 无鉴权 | none | 直接发送 |
| Bearer Token | bearer | token 填原值，自动加 Authorization: Bearer |
| API Key / 自定义 Header | api_header | 填 key_name、token；prefix 可选 |
| API Key / 查询参数 | api_query | 填 key_name、token；prefix 可选 |
| Basic 用户名密码 | basic | 填 username、password |
| 登录后 Token | login_token | 配置 login，提取 token_header 或 token_path，后续通过 key_name Header 发送 |
| 登录后 Cookie | login_cookie | 配置 login，Cookie 自动存于当前会话 |

登录请求支持 method、path、query、headers、body_type、body。变量 `{{password}}` 是原始密码，`{{password_md5}}` 是 UTF-8 密码的 32 位 MD5（只用于协议兼容，不代表安全加密）。

Token 可从 `token_header` 指定的响应头提取，或从 `token_path` 指定的 JSON 字段提取。支持 `data.accessToken`、`data.0.token` 这样的点路径。可用 `success_path` 和 `success_value` 检查登录业务状态，避免 HTTP 200 的登录失败被误判成功。

WVP 示例采用 `/api/user/login`、MD5 密码和 `access-token` 响应头。依据 [WVP 用户接口源码](https://github.com/648540858/wvp-GB28181-pro/blob/master/src/main/java/com/genersoft/iot/vmp/vmanager/user/UserController.java)。不同版本可能返回 `accessToken` 或 `data.accessToken`，请按现场文档调整。设备列表路径也是版本示例，需在现场确认。

Cookie 示例演示任务调度平台的登录会话；XXL-JOB 不同版本、调度中心与执行器的鉴权方式可能不同。调度中心通常用登录 Cookie，执行器可能需要自定义 accessToken Header；应按现场接口文档选择对应模式，不把两者混用。

### 凭据、网络和响应处理

- 当前服务连接保存至 `%LOCALAPPDATA%\VideoUploadCheck\api_profiles.json`。勾选“记住密钥”时，整个连接配置（包括登录模板）使用 Windows DPAPI 加密；不同配置名称分开保存。取消勾选后只保存非敏感连接字段，移除该配置之前保存的密钥及自定义登录模板。
- 登录得到的 Token/Cookie 只在当前窗口会话内保存，关闭、清除会话或改变连接配置后需要重新登录。手动填写的 Token/API Key 可以选择加密记住。
- 切换接口示例会根据 profile_name 恢复该服务已保存的连接；修改配置名称后可点击“恢复已保存连接”。成功调用后自动保存连接，保存失败不影响已返回的响应。
- 不把真实密码或密钥写进 api_requests.json；使用鉴权输入框或模板变量。请求和响应显示会遮盖常见凭据字段、已知凭据值及 secret 参数；原始响应仅在内存中处理。对未知字段的业务敏感信息不能保证自动识别。
- HTTPS 默认校验证书和主机名，可选择自有 CA 文件；不提供关闭证书验证选项。HTTP 请求不加密。默认不用系统代理，内网接口可直连；确实需要代理时勾选“使用系统代理”。
- 超时可设 1–120 秒；网络连接与读取可能分别等待，DNS 时间也由操作系统控制。响应及请求体上限为 2 MB；不适用于视频流或大文件下载。非文本响应只显示类型和字节数。
- 3xx 重定向只显示状态，不自动跟随，避免转发凭据或重放写请求。4xx/5xx 响应也可以查看。不自动重试、不自动重新登录后重发；401/403 时先核对凭据。
- 接口写操作由远端系统处理，不具备数据库更新窗口的事务回滚能力。超时或连接断开不能证明服务端没有执行；先查询核实，避免任务被重复触发。
- 日志只记录事件、HTTP 状态和异常类型，不记录 URL、Header、Cookie、请求体、响应体或凭据。

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

只对本次脚本执行设置策略，不修改系统策略。打包输出在 `dist`，需分发 EXE、queries.json、updates.json、updates.example.json 和 api_requests.json。EXE 未做代码签名；如客户组织有签名要求，交付前按组织流程签名。

GitHub Actions 会在 main 推送、PR 或手动触发时执行测试和 Windows 打包，构建成功后在 Actions 页面下载产物。本项目不自动发布 GitHub Release。

## 验证状态

测试覆盖参数绑定、日期范围、SQL 排序与 LIMIT、失败清理、配置迁移、加密失败恢复、结果保留、CSV 防护和日志脱敏。运行：

```powershell
python -m unittest discover -s tests -v
```

Windows 构建任务还会执行真实 Tk 窗口测试和 DPAPI 跨进程恢复测试。独立 MySQL 8.0 工作流使用临时数据库检查排序、截断、重复列名、参数绑定和错误后重连，并验证更新预览不写入、成功提交、同值更新、并发冲突及多行更新中途失败回滚。测试凭据仅用于 CI 临时容器，不连接任何客户数据库。

本地无 Windows 桌面或 MySQL 时会明确跳过对应测试；提交后以 Actions 结果为准。真实业务 SQL 正确性、现场网络和 EXE 用户验收仍需在客户环境核对。

API 测试使用本地临时 HTTP 服务验证 URL 编码、请求体类型、各类鉴权、登录 Token、Cookie、错误响应、响应大小限制、重定向不重放以及请求脱敏；不调用任何客户接口。Windows 任务还验证 API 窗口状态和预览。平台真实账号、现场接口版本及业务效果需在现场核对。


### v0.4.1：API 草稿与连接选择

- “连接配置名称”支持下拉选择已保存连接，选中后点击“恢复已保存连接”；也可以输入新名称后保存。
- 修改请求 JSON、登录 JSON、参数或服务配置后，点击“保存接口草稿（加密）”。下次打开相同名称接口会自动恢复，不需要再次输入。保存草稿不会发送请求。
- 切换接口、重新加载配置时，本次窗口中的编辑会保留。关闭时若还有未保存修改，会提醒；取消关闭后可逐个切换接口保存。
- 草稿包含当前接口的服务信息、密码、Token 和参数，即使未勾选“记住密钥”，主动保存草稿仍会把这些内容整体加密保存。只想保存非敏感连接信息时，请使用“保存当前连接”并取消“记住密钥”。
- 草稿位于 `%LOCALAPPDATA%/VideoUploadCheck/api_drafts.json`，仅原 Windows 用户可解密，不随 EXE 打包，不提交 Git。登录后的临时 Cookie/Token 会话不保存，重启后需要重新登录。
- 草稿按接口名称匹配，优先于同名 api_requests.json 的请求默认值。重新加载不会抛弃草稿；需要完全恢复原始模板时，关闭窗口后备份并移走 api_drafts.json，再重新打开。更换接口名称会视为新接口。
- 加密或写入失败时会提示错误，编辑内容继续留在窗口中；不会以明文降级保存。此功能的跨次保存仅支持 Windows。
- 升级时保留你修改过的 queries.json、updates.json 和 api_requests.json，不要直接用示例覆盖。
