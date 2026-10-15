# Session to MD

把当前 Codex 会话及分支前的历史导出为一个 Markdown 文件，只保留用户提问和 Agent 的完整正式回答，便于另一个 Agent 阅读全局上下文与实践记录。

**手动调用，无需补充参数；不摘要、不改写、不主动截断。**

## 使用效果

在要导出的会话中手动选择 **Session to MD**，或发送：

```text
$session-to-md
```

skill 默认导出当前这段对话，不需要填写 session ID、提示词或输出目录。完成后，Agent 返回可点击的 Markdown 文件链接、所在文件夹路径，以及 Windows 资源管理器定位命令。

输出文件位于当前工作目录的 `session-exports`：

```text
session-exports/session-<current-session-id>.md
```

文件名中的 ID 始终属于发起导出的当前会话。读取父历史不会把文件名改成父会话 ID。

## 安装

需要 Python 3.10 或更高版本，且 Codex 能在终端中运行 `python`。导出脚本只依赖 Python 标准库，无需单独的 API Key 或第三方 Python 包。

将仓库克隆到你使用的 skills 目录：

```powershell
git clone https://github.com/Woif-sha/session-to-md.git
```

也可以从 [Releases](https://github.com/Woif-sha/session-to-md/releases) 下载 `session-to-md-vX.Y.Z.zip`，将其中的 `session-to-md` 文件夹放入你使用的 skills 目录。

如果 Codex 的 skill 列表尚未出现它，重新启动 Codex 后再手动选择。

## 导出格式

生成的标题、元信息和角色标记使用英文；问答正文保持原始语言与 Markdown 格式。

```markdown
# Session <current-session-id>

- Session: `<current-session-id>`
- Created: <ISO 8601 timestamp with timezone>
- Exported: <ISO 8601 timestamp with timezone>
- Lineage: `<root-session-id> → <current-session-id>`

## History · <root-session-id>

### User

分支前的原始问题。

### Agent

对应的完整正式回答。

## Current · <current-session-id>

### User

当前分支的问题。

### Agent

对应的完整正式回答。
```

没有分支时只出现 `Current`。多级分支依次呈现各段 `History`；同一 session 的多个 rollout 文件合并为同一个会话段。

## 保留与排除

| 内容 | 处理方式 |
| --- | --- |
| 用户实际提问、补充、纠正及明确引用的材料 | 保留原文 |
| Agent 的正式回答 | 保留完整正文、代码块、列表、表格与链接 |
| Agent 过程播报和中间进度回复 | 排除 |
| 系统／开发者指令、自动注入的 AGENTS.md 和环境上下文 | 排除 |
| 工具调用、工具输出及隐藏推理 | 排除 |
| 同一消息的镜像日志和重复继承前缀 | 合并 |
| 用户确实重复提出的问题与实际重复给出的回答 | 保留 |

正常问答中对 AGENTS.md 的讨论不会仅因出现这个文件名而被删除。内容来源标记用于区分用户原文和注入规则；旧记录使用完整注入包络识别。

## 分支历史如何处理

导出范围为通向当前会话的这一条历史链：父会话截至分叉点的问答，再接子分支新增的问答。父会话在分叉后新增的内容和其他兄弟分支不会混入。

导出器根据 `history_base` 的 rollout 身份、字节边界和 ordinal 边界读取分页历史，也能读取 `archived_sessions` 中被引用的源文件。对复制历史的旧式 fork，通过父历史前缀识别消息归属。

正式回答优先根据 `phase: final_answer` 识别。旧日志没有 phase 时，使用回合完成事件确认已有的原始回答，不把完成事件或压缩摘要当成正文重新生成。

## 调用边界与限制

- **仅用户手动调用。** 已关闭隐式调用，规则也禁止其他 Agent、skill 或自动流程主动触发导出。
- 默认从 `CODEX_THREAD_ID` 取得当前会话 ID；不会猜测最近修改的文件。`CODEX_HOME` 默认为 `~/.codex`。
- 需要本地可读的原始会话文件。切换电脑或环境后，必须能访问被引用的父历史；本工具不会从其他设备或云端拉取记录。
- 当前支持 `.jsonl`，暂不支持 `.jsonl.zst` 压缩日志。
- 缺少父历史、存在歧义、边界无效、原始正式回答缺失或无法确认回答阶段时，导出明确失败，不发布伪完整文件。
- 源日志已经丢失或截断的内容无法恢复。导出快照也不会包含导出完成后才产生的回复。
- 这是文本导出器，不复制图片、音频或其他附件文件。

导出过程只读取本地 Codex 数据，不修改原始会话，不上传对话。生成的 Markdown 仍可能包含问题、回答中的代码、路径或其他私人信息；分享范围由你决定。

## 更新

Git 安装可在 skill 目录中执行：

```powershell
git pull --ff-only
```

安装包用户可下载新版本后替换 skill 文件夹，保留自己需要的导出文件。

## 开发与验证

```powershell
python -m unittest discover -s tests -v
```

测试覆盖正式回答选择、注入过滤、用户引用保留、镜像去重、多级分支、同 session 续写和缺失原文报错。

版本以 [VERSION](VERSION) 为准，变更记录见 [CHANGELOG.md](CHANGELOG.md)。GitHub Actions 的手动发布工作流会先运行测试、构建安装包，再创建对应标签与 Release；已有版本不覆盖。

## 来源与许可证

历史边界解析参考并改编自 [Ann-Diana/codex-project-chat-exporter](https://github.com/Ann-Diana/codex-project-chat-exporter)。本项目独立维护，面向单个会话的纯问答 Markdown 导出，不包含上游的批量项目归档或文档渲染器。具体来源见 [references/upstream.md](references/upstream.md)。

使用 [MIT License](LICENSE)，保留上游版权声明。本项目非 OpenAI 官方项目。
