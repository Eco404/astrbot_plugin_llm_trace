# Changelog

本项目的所有重要变更都会记录在此文件中。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Changed

- 将插件元数据版本统一为不带 `v` 前缀的 SemVer 格式。

## [0.1.4] - 2026-07-19

### Changed

- 压缩 WebUI 顶部标题区高度，为记录列表和事件详情保留更多空间。

## [0.1.3] - 2026-07-19

### Changed

- WebUI 改为贴合 Plugin Page 可用视口，避免宿主页面产生额外纵向滚动。
- 最近记录和详情区域使用独立滚动，长列表不再超出页面。

## [0.1.2] - 2026-07-19

### Added

- 支持从 WebUI 删除单条 Trace 记录。
- 增加兼容 iframe 环境的页面内确认对话框。

### Changed

- 单条记录删除按钮改为红色垃圾桶图标。

## [0.1.1] - 2026-07-19

### Added

- 增加输入总字符、本轮提示、系统提示、历史上下文、最终输出及推理/工具结果字符统计。
- 增加 `/llmtrace probe` 指令，用于检查群聊配置、插件启用状态和 Provider。

### Fixed

- 为“复制 JSON”增加 iframe 和非安全上下文下的剪贴板降级方案。

## [0.1.0] - 2026-07-18

### Added

- 记录 LLM 请求、Agent 上下文、工具调用和最终响应。
- 提供实时 Trace WebUI、搜索、清空和自动保留能力。
- 对身份标识和常见敏感字段进行哈希或脱敏处理。
- 使用 SQLite 将调试数据保存到 AstrBot 插件数据目录。
