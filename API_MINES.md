# API 按煤矿选择连接（v0.10.0）

在 `api_requests.json` 的某个接口对象中增加 `connections`。界面在接口选择框下方新增煤矿连接下拉框，流程向导的 API 步骤也有同样功能。参考 `api_mines.example.json`，复制其中对象加入现场 `api_requests.json` 数组；示例不会自动启用。

```json
"connections": [
  {
    "id": "limin",
    "name": "利民煤矿",
    "profile": {
      "base_url": "http://192.0.2.9:8080/xxl-job-admin",
      "username": "client_ops",
      "password": ""
    }
  },
  {
    "id": "huangbaici",
    "name": "黄白茨煤矿",
    "profile": {
      "base_url": "http://192.0.2.10:8080/xxl-job-admin",
      "username": "client_ops",
      "password": ""
    }
  }
]
```

示例 IP 为文档保留地址，需要替换。此片段不是独立完整 JSON 文件，放在接口对象内，与 `profile`、`request`、`params` 同级。id 与 name 在一个接口中必须唯一。可用 `default_connection: "limin"` 设置初始煤矿；空值或省略表示“接口默认连接”。不会自动登录或执行请求。

公共 `profile` 配置鉴权方式和登录模板，每个矿的 profile 覆盖公共设置，可覆盖 `auth_type`、`login` 等字段。选择煤矿时地址、username、password、token 不继承公共 profile 的这些字段，以免沿用另一个目标的凭据；缺少密码时回填空。登录 JSON 使用 `{{username}}` / `{{password}}`，不要把某个矿的用户名密码写死在公共登录请求体。

使用步骤：选择接口 → 选择煤矿 → 核对地址和账号 → 首次输入密码 → 勾选“记住密钥”并保存 API 连接 → 登录 → 发送并确认。下次选择同一煤矿可恢复本机保存的地址和凭据。不同煤矿和不同服务分别保存；数据库煤矿选择不会自动改变 API 目标，发送确认框仍显示实际 API 地址。

独立窗口的连接、草稿按煤矿隔离。切换清空原响应显示并清除旧 Cookie / Token 会话；返回某个矿的草稿不会恢复登录会话，必须重新登录。流程切换煤矿后当前结果及后续确认失效，参数回到本步默认值。结果待核实的步骤须先核实并解除限制，不能靠切换煤矿解锁。运行中禁用下拉框。单纯选择或保存连接不会请求接口。

`profile_name` 应准确区分服务，例如 `XXL-JOB` 和 `WVP`。多个操作想复用同一矿的已保存连接，需要相同服务名、连接 id 和有效 profile 配置。文件连接内容改变时原本机覆盖及草稿不再匹配，需重新填写 / 保存，以免旧目标覆盖新地址。连接配置加密仍依赖当前 Windows 用户；可配置明文初始 password，但该文件并不加密，真实密码不能提交公共仓库。更推荐初始 password 留空，在客户电脑首次输入后加密保存。

不配置 connections 的旧接口继续使用原有服务默认值和本机保存逻辑。升级保留现场 SQL、API 和流程文件，不要被安装包示例覆盖。重载接口配置后重新选择接口和煤矿。
