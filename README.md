# IRC适配器插件 for AstrBot

这是一个用于 AstrBot 的 IRC 平台适配器，可让你通过 AstrBot 连接到标准 IRC 服务器。

该插件当前定位为：

- 纯平台适配器
- 通过 AstrBot WebUI 直接配置和使用
- 无插件级额外配置
- 无聊天命令或管理命令

## 功能特性

- ✅ 支持通过 WebUI 配置多个 IRC 机器人实例
- ✅ 自动重连机制
- ✅ SSL/TLS加密连接
- ✅ 频道自动加入
- ✅ 私聊与频道消息统一转换为标准 `AstrBotMessage`
- ✅ 发送链同时兼容事件发送与 `send_by_session()`
- ✅ 昵称冲突自动处理
- ✅ 长消息自动分割
- ✅ 兼容部分 AstrBot 版本接口差异
- ✅ 完整的错误处理和恢复

## 安装

1. 将 `irc_adapter_plugin` 目录复制到 `AstrBot/data/plugins/` 目录下
2. 在 AstrBot 环境中安装插件依赖：`pip install -r requirements.txt`
3. 重启AstrBot

## 配置

本插件**没有插件级配置项**，因此插件配置页无需额外填写内容。

所有连接参数都通过 AstrBot 的机器人平台配置提供。

### 推荐方式：通过 WebUI 配置

1. 在 AstrBot WebUI 中添加新的机器人
2. 平台类型选择 `irc`
3. 填写服务器、昵称、频道等配置
4. 保存后启动机器人

如需多个 IRC 连接，请直接在 WebUI 中添加多个使用 `irc` 适配器的机器人实例。

### 平台配置示例

如果你使用配置文件方式管理机器人，可参考以下示例：

```yaml
platforms:
  - name: "irc_libera"
    type: "irc"
    config:
      server: "irc.libera.chat"
      port: 6667
      nickname: "your_bot_nick"
      username: "your_bot"
      realname: "AstrBot IRC Client"
      channels: "#test,#another"
      password: ""  # 可选，服务器密码
      ssl: false
      ssl_verify: true
      group_wake_prefixes: ["your_bot_nick:", "@your_bot_nick "]
      reconnect_interval: 30
      max_reconnect_attempts: 5
```

### 常用配置项说明

- `server`：IRC 服务器地址
- `port`：IRC 服务器端口
- `nickname`：机器人昵称
- `username`：IRC 登录用户名
- `realname`：IRC 显示名称
- `channels`：启动后自动加入的频道，多个频道可用逗号分隔
- `password`：服务器密码，可选
- `ssl`：是否启用 SSL/TLS
- `ssl_verify`：是否验证 SSL 证书
- `group_wake_prefixes`：频道消息唤醒前缀，支持字符串逗号分隔或字符串数组，不配置时默认使用机器人昵称相关前缀
- `reconnect_interval`：断线重连间隔（秒）
- `max_reconnect_attempts`：最大重连次数

## 使用

本插件不提供聊天命令或管理命令。

所有启停、连接参数和多机器人配置都应在 AstrBot WebUI 中完成。

IRC 侧以**纯文本消息**为主。部分消息组件在转换到 IRC 时会降级为文本形式发送。

### 频道唤醒规则

机器人在频道中**不会回复所有消息**。

只有当消息以前缀方式明确唤醒机器人时，才会进入 AstrBot 的对话流程。默认支持以下形式：

- `botnick: 你好`
- `botnick：你好`
- `@botnick 你好`
- `botnick, 你好`
- `botnick，你好`
- `botnick 你好`

其中 `botnick` 为当前 IRC 机器人昵称。

如果你希望自定义频道唤醒格式，可通过平台配置项 `group_wake_prefixes` 覆盖默认值。

### 当前发送模型

本适配器当前按 AstrBot 官方平台适配器文档实现：

- 入站 IRC 消息先转换为 `AstrBotMessage`
- 再封装为 `IRCEvent`
- 由 `self.commit_event(...)` 提交给 AstrBot Core
- 回复阶段优先走事件对象的 `send()`
- 主动消息或会话发送由平台适配器的 `send_by_session()` 兜底

如果需要排查“LLM 已生成但 IRC 未实际发出”的问题，可重点查看以下日志：

- `提交IRC事件:`
- `IRCEvent.send target=...`
- `IRC平台发送: session=...`
- `IRC原生发送: target=...`

### 支持的IRC服务器

- Libera.Chat (irc.libera.chat:6667)
- OFTC (irc.oftc.net:6667)
- 任何标准的IRC服务器

## 开发

### 依赖

- Python 3.8+
- `irc>=20.0.0`

### 文件结构

```text
irc_adapter_plugin/
├── metadata.yaml          # 插件元数据
├── requirements.txt       # 依赖声明
├── _conf_schema.json     # 插件配置Schema（当前为空）
├── README.md            # 说明文档
├── __init__.py          # 包初始化
├── main.py             # 插件主类
├── irc_adapter.py      # IRC平台适配器
└── irc_event.py        # IRC事件处理
```

### 测试

可使用 `test_plugin.py` 做基础结构与导入检查。

如需联机验证，可按以下方式测试：

1. 启动本地 IRC 服务器（如 `ngircd`）或准备可用的测试服务器
2. 在 AstrBot WebUI 中添加并配置一个 `type=irc` 的机器人
3. 启动该机器人实例
4. 使用 IRC 客户端进入相同频道进行收发测试

## 故障排除

### 常见问题

1. **连接失败**
   - 检查服务器地址和端口
   - 检查防火墙设置
   - 尝试关闭SSL

2. **昵称已被使用**
   - 插件会自动添加下划线重试

3. **消息被截断**
   - 消息超过400字符会自动分割

4. **SSL证书验证失败**
   - 设置 `ssl_verify: false` 禁用证书验证

5. **消息格式与预期不完全一致**
   - IRC 本身以纯文本为主，部分 AstrBot 消息组件会被降级为文本

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request！
