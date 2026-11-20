# 项目 Agent Skills 说明

本文记录从两个公开仓库导入的项目级 Skills，均位于 `.agents/skills/`。Cursor 来源提交：`ea32d9562a1c9f077d7cdd3be53d07383eb18866`。Matt Pocock 来源提交：`49dd158d1076134a641b33efb035946536778336`。

| Skill | 来源目录 | 作用 |
| --- | --- | --- |
| `tdd` | `pstack/skills/tdd/` | 修复有明确、低成本复现路径的缺陷时，先写并运行失败的回归测试，再修代码并重跑。测试代价过高或容易失真的情况，改用更合适的验证。 |
| `principle-fix-root-causes` | `pstack/skills/principle-fix-root-causes/` | 调试时先复现，再沿因果链找根因；避免用临时保护逻辑掩盖症状，并检查同类问题。 |
| `verify-this` | `cursor-team-kit/skills/verify-this/` | 把“修好了”等主张改写成可证伪条件，用一致的前后基线和产物对比，给出 VERIFIED、NOT VERIFIED 或 INCONCLUSIVE 结论。 |
| `technical-writing` | `pstack/skills/technical-writing/` | 编写或评审文档、RFC、README、PR 描述和提交说明；按 Diátaxis 选择文档类型，并提升句子清晰度和一致性。 |

## Matt Pocock Skills

| Skill | 来源目录 | 作用 |
| --- | --- | --- |
| `diagnosing-bugs` | `skills/engineering/diagnosing-bugs/` | 按复现、缩小范围、假设、取证、修复和回归测试的流程排查难复现缺陷。 |
| `domain-modeling` | `skills/engineering/domain-modeling/` | 讨论和完善领域术语，使用配套格式维护 Glossary 与 ADR。 |
| `code-review` | `skills/engineering/code-review/` | 从项目规范和原始需求两个角度并行评审改动。 |
| `writing-for-agents` | `skills/productivity/writing-for-agents/` | 编写或修改 Skills、AGENTS.md、CLAUDE.md 等 Agent 文档。 |
| `grill-with-docs` | `skills/engineering/grill-with-docs/` | 通过访谈厘清方案，同时沉淀术语表和设计决策。 |
| `setup-matt-pocock-skills` | `skills/engineering/setup-matt-pocock-skills/` | 配置工程 Skills 使用的 Issue Tracker、标签和领域文档布局。 |
| `grill-me` | `skills/productivity/grill-me/` | 通过多轮追问厘清计划或设计。 |
| `grilling` | `skills/productivity/grilling/` | 为其他访谈类 Skill 提供追问流程。 |
| `handoff` | `skills/productivity/handoff/` | 为后续 Agent 生成精简交接文档。 |
| `teach` | `skills/productivity/teach/` | 在多轮学习过程中记录目标、资源和学习进度。 |
| `to-questionnaire` | `skills/productivity/to-questionnaire/` | 把需要他人回答的决策问题整理成问卷。 |
| `wait-what` | `skills/productivity/wait-what/` | 用项目术语和简明语言重新解释未理解的消息。 |

## 使用边界

这些文件是 Agent 的工作指引，不是 Python 或应用运行依赖。`tdd`、`principle-fix-root-causes` 和 `technical-writing` 标记为需要显式调用；`verify-this` 可按其描述在验证任务中触发。

`verify-this` 的示例产物路径使用 `/tmp/verify-this/`。Windows 环境执行时应改用合适的本机临时目录。`technical-writing` 指向另一个 `unslop` Skill；本次没有安装 `unslop`，其余写作规则仍在本 Skill 文件中。

`setup-matt-pocock-skills` 尚未运行。运行时需要选择 Issue Tracker、标签和文档路径；选择 GitHub 或 GitLab 后，它可能创建缺少的远端标签。`diagnosing-bugs` 附带 Bash HITL 脚本，在 Windows 上使用该脚本时需要 Bash 环境。项目级 `grilling` 与用户级同名 Skill 并存，本次没有覆盖用户目录。
