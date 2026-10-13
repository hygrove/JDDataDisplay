# 分诊标签（Triage Labels）

技能以五个规范的分诊角色来表述。本文件把这些角色映射到本仓库问题追踪器中实际使用的标签字符串。

本仓库用**本地 markdown** 追踪器（见 `issue-tracker.md`），没有原生标签——标签以 issue 文件顶部的 `Status:` 一行的取值来表达。

| mattpocock/skills 中的标签 | 我们追踪器中的标签 | 含义 |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | 维护者需要评估这个 issue |
| `needs-info`               | `needs-info`         | 等待报告者提供更多信息 |
| `ready-for-agent`          | `ready-for-agent`    | 已完整描述，可供一个 AFK agent 接管 |
| `ready-for-human`          | `ready-for-human`    | 需要人工实现 |
| `wontfix`                  | `wontfix`            | 不会处理 |

当技能提到一个角色（例如"应用 AFK-ready 分诊标签"），使用本表中对应的标签字符串。

编辑右列以匹配你实际使用的任何词汇。
