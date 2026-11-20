# Issue Tracker：本地 Markdown

本项目将需求规格和工作事项记录为 `.scratch/` 下的 Markdown 文件。

## 文件约定

- 每个功能使用一个目录：`.scratch/<feature-slug>/`。
- 功能规格写入 `.scratch/<feature-slug>/spec.md`。
- 实施事项按独立文件记录：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号，不把多个事项合并到一个文件。
- 讨论和补充记录追加在事项文件末尾的 `## Comments` 标题下。

## 使用方式

- 需要发布规格或事项时，在对应功能目录中创建 Markdown 文件。
- 需要读取事项时，读取用户提供的文件路径或事项编号对应的文件。

## 大型工作流规划文件（Wayfinding）

如果后续使用 `/wayfinder`：

- 计划地图写入 `.scratch/<effort>/map.md`，包含 Notes、Decisions-so-far 和 Fog。
- 子事项写入 `.scratch/<effort>/issues/<NN>-<slug>.md`，从 `01` 开始编号。
- 子事项可用 `Type:` 标记 `research`、`prototype`、`grilling` 或 `task`。
- 用 `Status:` 标记 `claimed` 或 `resolved`。
- 用 `Blocked by: NN, NN` 记录阻塞关系；所有依赖事项解决后，当前事项才可开始。
- 领取事项时先写入 `Status: claimed`。完成后在 `## Answer` 下记录结论，将状态改为 `resolved`，并把决策摘要链接补到地图。