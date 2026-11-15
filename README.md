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

`on_llm_request` 只代表 Agent 启动前的公开请求。Agent 内部的每次 Provider 调用、重试，以及 SDK 最终 HTTP JSON 不会作为公开事件钩子提供。

### 实验性 Provider 深度探针

`experimental_provider_probe` 默认关闭。开启后，插件会运行时包装已加载的 Chat Provider，记录 `text_chat` 和 `text_chat_stream` 调用，即使当前对话的插件集未启用 LLM Trace。探针记录会以 `provider_probe` 平台和 `provider.*` 时间线事件单独展示；当探针能将调用关联到主 Trace 时，Provider 发生切换还会在主 Trace 中增加 `provider.fallback` 事件。`abort_signal` 等运行时控制对象只记录类型和状态摘要，不会递归保存事件循环内部状态。

当同一次对话原本已启用 LLM Trace 时，主链 Trace 与对应的 Provider 探针 Trace 会并存；这是为了保持实验性探针与公开 Agent 生命周期互不混写。

这是非侵入宿主源码条件下的调试能力，不保证完整覆盖：Provider 热重载的短暂窗口、其他插件直接使用厂商 SDK/HTTP 请求、已缓存的 Provider 方法，以及 Provider 内部的重试和 Key 切换都可能无法细分或遗漏。开启前请确认全局采集和内容策略符合预期。
