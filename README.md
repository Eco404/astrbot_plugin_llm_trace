# astrbot_plugin_llm_trace

LLM Trace 为 AstrBot 提供请求、工具调用和最终响应的可视化调试时间线。

当前版本记录 AstrBot 公开事件层的数据：

- 最终 `ProviderRequest` 快照
- Agent 开始和结束时的消息上下文
- 工具名称、参数和结果
- 最终 `LLMResponse`、token usage 和可选 `raw_completion`
- Trace 实时页面、搜索、删除和自动保留
- 输入、系统提示、历史上下文、最终输出和推理/工具结果字符统计

## 安装

将插件目录放入：

```text
AstrBot/data/plugins/astrbot_plugin_llm_trace
```

然后在 AstrBot WebUI 中重载插件。插件要求 AstrBot `>=4.24,<5`。

插件默认关闭采集。可在插件配置、Plugin Page，或管理员指令中开启：

```text
/llmtrace on
/llmtrace off
/llmtrace status
/llmtrace clear
/llmtrace probe
```

如果某个群聊没有产生记录，请在该群发送 `/llmtrace probe`。它会显示群聊
UMO、当前配置的插件集、唤醒状态和 Provider。若指令本身无法识别，通常表示
该群命中的配置文件没有启用本插件。

## 数据与隐私

Trace 数据保存在：

```text
data/plugin_data/astrbot_plugin_llm_trace/traces.db
```

身份标识默认使用 SHA-256 摘要；API Key、Authorization、Cookie、密码等字段会递归脱敏；图片和音频引用只保存长度与哈希。调试数据仍可能包含聊天记录、系统提示词和工具输出，请只在需要时开启，并设置合理的保留时间。

## 当前边界

`on_llm_request` 只代表 Agent 启动前的公开请求。Agent 内部的每次 Provider 调用、fallback、重试，以及 SDK 最终 HTTP JSON 尚未由公开事件钩子暴露。本插件后续会把“Provider 深度探针”作为独立的实验模式加入，避免首版通过 monkey patch 影响正常对话。
