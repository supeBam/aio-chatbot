# AIO 工作流字段、变量与状态说明

本文对应 [当前规格](spec.md)、[流程说明](flow.md) 和
[生成脚本](../../dify/build_multi_business_workflow.py)。
字段类型、默认值和写入行为以当前生成实现为准。

## 1. 先区分四类数据

| 类别 | 生命周期 | 示例 |
| --- | --- | --- |
| API 与开始节点入参 | 每次请求重新提供 | `query`、`event_type`、`bn_plan_codes` |
| 会话变量 | 同一 Dify 会话跨轮次保存 | `bn_plans_tags`、`bn_needs` |
| 节点临时输出 | 本轮流转，不自动跨轮次保存 | `snapshot`、`bundle`、候选原文 |
| 前端动作与聊天流 | 后端转发给前端 | `request_address`、自然语言文本分片 |

`snapshot` 和 `bundle` 不是新增会话状态，也不是另一个持久化大 JSON。
LLM 不接收包含所有业务的快照，只接收当前业务的需求与候选。

## 2. API 与开始节点入参

### API 外层

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `inputs` | `object` | 只包含下表两个自定义开始节点字段 |
| `query` | `string` | 必须非空；普通消息为用户原话，地址事件为后端固定说明 |
| `response_mode` | `string` | 使用 `streaming` 以接收节点事件和文本流 |
| `conversation_id` | `string` | 首次为空或省略，后续使用同一返回值 |
| `user` | `string` | 应用后端维护的稳定用户标识，不能随地址事件改变 |

本工作流直接使用的 Dify 系统变量是 `sys.query`。
`sys.conversation_id` 等是平台系统信息，不是自定义会话变量；
应用后端使用 API 返回的会话和运行标识管理顺序及事件去重。

### 自定义开始节点字段

| 变量名 | Dify 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `event_type` | `select` | `message` | 本轮事件类型 |
| `bn_plan_codes` | `paragraph` | `""` | 英文逗号分隔的全部 BN 可售编号，长度上限 8192 |

| 事件枚举 | 触发时机 | 编号内容 | 是否识别业务 |
| --- | --- | --- | --- |
| `message` | 用户聊天 | 必须为空 | 是 |
| `address_add` | 首次地址查询成功 | 可售编号；无套餐则为空 | 否，固定 BN |
| `address_change` | 更换地址查询成功 | 新可售编号；无套餐则为空 | 否，固定 BN |

没有清除地址事件和查询状态入参。
后端查询失败时不调用成功地址事件，由前后端展示失败。
地址事件的 `query` 不加入待处理用户消息，也不作为背景或就绪证据。

## 3. 全部会话变量：12 个

| 变量名 | 类型 | 默认值 | 内容与主要写入时机 |
| --- | --- | --- | --- |
| `current_business` | `string` | `""` | 当前 `BN`、`MS` 或 `XE`；有效路由或地址事件写入 |
| `bn_scope_codes` | `array[string]` | `[]` | 当前可信地址范围；每个成功地址事件替换 |
| `routing_context` | `object` | 见第 5 节 | 尚未明确业务的消息与问题 |
| `bn_plans_tags` | `array[object]` | `[]` | BN 当前地址套餐标签；地址事件清空，批量加载后写入 |
| `bn_catalog_status` | `string` | `unloaded` | BN 标签加载状态 |
| `bn_needs` | `object` | 见第 4 节 | BN 偏好、使用背景、待处理需求与访谈状态 |
| `ms_plans_tags` | `array[object]` | `[]` | MS 标签；首次成功加载后复用 |
| `ms_catalog_status` | `string` | `unloaded` | MS 标签加载状态 |
| `ms_needs` | `object` | 见第 4 节 | MS 独立需求 |
| `xe_plans_tags` | `array[object]` | `[]` | XE 标签；首次成功加载后复用 |
| `xe_catalog_status` | `string` | `unloaded` | XE 标签加载状态 |
| `xe_needs` | `object` | 见第 4 节 | XE 独立需求 |

前述 11 个业务变量之外，`routing_context` 专门补足不明确业务时的跨轮澄清。
它不保存产品目录、推荐结果或其他审计信息。

### 套餐数组中的对象

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `plan_code` | `string` | 精确的套餐编号，不改写、不猜测 |
| `tag` | `array[string]` | 产品标签，沿用源文件字段名；不是用户偏好 |

对象只允许这两个字段，不能把 Markdown 或接口元数据混入标签数组。
相同数组中套餐编号和每款套餐的标签都不能重复。

### 加载状态枚举

| 枚举 | 含义 | 后续行为 |
| --- | --- | --- |
| `unloaded` | 尚未加载 | BN 无范围先弹表单，有范围或 MS/XE 则加载 |
| `ready` | 有效标签已加载，候选可用 | 复用数组，不重复请求 |
| `empty` | 成功但没有套餐 | 自然语言告知，不重复请求 |
| `error` | 地址结果无效、标签失败或范围不一致 | 明确提示，禁止推荐；已有有效编号时允许重试加载 |

BN 成功地址事件有编号时先置为 `unloaded`，空编号直接置为 `empty`。
非法新地址先清除旧数据并保留 `error`，不能静默回到旧地址。
加载状态不是需求就绪标记。

## 4. 三个需求对象的全部字段

`bn_needs`、`ms_needs`、`xe_needs` 使用相同结构：

```json
{
  "requirements": [],
  "next_requirement_id": 1,
  "questions": [],
  "next_question_id": 1,
  "last_validation_errors": [],
  "user_context": "",
  "pending_messages": [],
  "last_reply": "",
  "interview_pending": false,
  "ready_to_recommend": false,
  "last_recommended_codes": [],
  "interview_rounds": 0,
  "asked_topics": [],
  "last_interview_topic": "",
  "answer_history": []
}
```

| 字段 | 类型 | 解释与写入规则 |
| --- | --- | --- |
| `requirements` | `array[object]` | 最多100条当前有效自由需求；每项有 `id`、`text`、`attitude`、`evidence`，新提取输出还含 `priority`；不同可独立判断的维度分别保存，不要求匹配产品标签 |
| `next_requirement_id` | `integer` | 默认1，单调递增生成 `R1` 等编号，撤回后不复用 |
| `questions` | `array[object]` | 最多100条当前有效套餐咨询，每项为 `id`、`text`、`evidence`；例如是否附电话，不自动变成必须包含电话的购买条件 |
| `next_question_id` | `integer` | 默认1，单调递增生成 `Q1` 等编号；更新沿用编号，撤回后不复用 |
| `last_validation_errors` | `array[string]` | 最近一次条目、背景或产品决策校验的具体原因；不作为用户事实 |
| `user_context` | `string` | 用户明确表达的预算、人数、场景等背景；模型同次提取，Code 校验证据后更新 |
| `pending_messages` | `array[string]` | 尚未提取的原话，包括填地址前需求；成功处理后清空，条目失败时保留；澄清时明确部分已提交且原话进入历史 |
| `last_reply` | `string` | 上一轮完整自然语言回复；流式回复完成后保存 |
| `interview_pending` | `boolean` | 上一轮确实提出了访谈或需求澄清；不是泛泛的“有上一轮回复” |
| `ready_to_recommend` | `boolean` | 仅由已校验产品决策设为真；信息充分可零轮推荐，关键歧义和数据错误阻止推荐 |
| `last_recommended_codes` | `array[string]` | 上次成功展示的编号，保留展示顺序；地址变化或错误清空 |
| `interview_rounds` | `integer` | 默认 `0`，范围 0–4；实际回答本业务产品访谈或需求澄清后加一，不按标签数计数；3轮是可选访谈上限而非最低要求 |
| `asked_topics` | `array[string]` | 已实际展示的访谈主题，不重复；用于阻止换措辞重问 |
| `last_interview_topic` | `string` | 上一轮产品访谈或需求澄清的动态主题键；其他回复或地址事件清空 |
| `answer_history` | `array[object]` | 最近最多 20 条成功处理的用户原话及对应问题；不依赖模型是否更新背景 |

每条需求的态度为 `interested`（感兴趣）、`uninterested`（不感兴趣）或 `indifferent`（有无均可）。
`priority` 为 `required`（明确不可妥协）、`preferred`（最好、倾向）或 `unspecified`（未声明强度）。
态度与强度分别表达，不把普通意愿或事实咨询自动升级为硬条件；旧条目省略强度时按未声明处理。
速度、预算、合约、订阅分别新增或修改，保留用户明确的或、二选一、组合及条件关系，不拆成错误的同时满足。
咨询也参与全目录候选查找及原文核对，但不作为自动淘汰条件，不增加内部分类确认问题。
入口兼容缺少 `questions` 和 `next_question_id` 的旧会话，补齐空列表与起始编号，保留已有需求、证据及问答。
缺少其中一个字段仍是损坏状态，不静默修复。旧复合需求不猜测拆分，下游对其编号整体评估一次。
未提及不等于平淡；`update` 按已有ID原子替换同一需求，`remove` 撤回，历史不得恢复旧事实。
旧版三个标签列表在入口迁移为自由需求，保留原文与态度，不受新目录约束；
旧标签没有逐项用户引用，迁移后的 `evidence` 为空，不能据此声称有可核验用户原话。
每业务最多 20 条待处理消息，超出时明确报错，不静默截断。
本版不保存完整聊天日志，但代码会保存最近 20 条成功处理的用户原话；
较旧的问答从该滚动记录移出，长期事实仍依赖偏好和 `user_context`。
即使 `context_update` 为 `null`，刚回答的场景、设备描述和“没要求”也不会立即丢失。
原话记录不是永远有效的事实清单；用户后续明确改口或撤回时，以最新表达为准。

### `answer_history` 对象

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `topic` | `string` | 常规访谈主题，或 `initial`（初始需求）、`refinement`（推荐后调整） |
| `question` | `string` | 回答常规访谈时保存实际上一轮问题；初始需求和推荐后调整为空 |
| `answer` | `string` | 用户原话，不改写、不推断“3到5台”究竟指电脑还是设备总数 |

同一待处理批次可保存多条原话，但最多增加一轮常规访谈计数。
JSON结构无法解析时保留待处理原话。条目级失败或需要澄清时仍记录真实问答，
独立明确条目可以提交；错误条目和歧义部分不能升级为已确认需求。

### 动态产品追问

不再维护固定主题枚举、问句或顺序，也不对自由需求使用产品标签正则判断是否已回答。
`shortlist` 在分支之前结合目录差异、当前需求和实际问答决定是否值得再问。
主题键为最多500字符的非空文本；同一语义须沿用已有键，不换词规避重复限制。
代码校验类型、长度及候选编号，不能证明主题与用户目标的语义关联或识别所有同义主题。

追问只需给出待确认信息、对选择的影响及阻塞标记，不再复制完整标签或填写额外引用编号。
用户需求与自由标签由模型语义比较，代码按所选 `plan_code` 取回对应资料，不让模型拼接套餐标签。
标签只是初筛和判断追问价值的线索，不是承诺条款；最终价格、优惠和条件仍核对对应原文。
已知速度与预算即可初步比较，未知可选偏好、无偏好或没有预算内套餐不强制继续访谈。
重复的非阻塞主题、或已回答至少3轮后仍提可选问题时，代码改为本轮比较。
真实需求冲突不能被上限覆盖；重复阻塞问题若没有提取阶段的新歧义说明则校验失败。
最终 LLM 仅围绕已批准意图自然表达一个问题，不使用固定问句、不提前承诺马上推荐。
产品关联、冲突识别和自然话术仍须真实模型验收，不将代码结构校验视为语义安全保证。

### 地址事件对 BN 需求的影响

| 字段 | 处理 |
| --- | --- |
| 自由需求及态度、`user_context`、`pending_messages`、问答原话和访谈轮数及已问主题 | 保留 |
| `last_reply`、`last_recommended_codes` | 清空 |
| `interview_pending`、`last_interview_topic` | 分别设为 `false`、空字符串，不继续引用旧地址问题 |
| `ready_to_recommend` | 不因地址事件升级，后续有真实待处理需求时再评估 |

MS、XE 的需求对象不因 BN 地址变更而修改。

## 5. 业务澄清对象

`routing_context` 默认值：

```json
{"messages":[],"question":""}
```

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `messages` | `array[string]` | 业务不明确时待澄清的用户原话，最多 20 条 |
| `question` | `string` | 先保存路由模型的澄清问题，流式回复完成后替换为最终实际话术 |

例如“想要Netflix”不能确定是独立订阅还是宽带附带时，保存原话和问题；
用户回答“单独订阅”后，将两条真实消息交给 XE，清空该澄清对象。
真正的多业务购买按 `business_requests` 中的原话片段分别进入对应 `pending_messages`，
不是将整句话复制给所有提及的业务；本对象不作为任务队列。

## 6. 模型结构化输出协议

### 业务识别模型

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `business` | `string` | `BN`、`MS`、`XE`，不明确时为空 |
| `mentioned_businesses` | `array[string]` | 提及的业务，不重复；仅为元数据，不触发消息分发 |
| `business_requests` | `array[object]` | 默认 `[]`；只有明确同时独立购买多个业务时，给出各业务的 `business` 和 `messages` 原话片段 |
| `clarification` | `string` | 需要确认业务时的问题，其他时候为空 |
| `intent` | `string` | `shopping` 或 `consult` |

`shopping` 包含访谈回答、套餐事实查询、比较和推荐；
`consult` 仅为不依赖具体套餐的一般概念。
地址事件由固定 Code 输出同样结构，不调用路由模型。
没有明确分片时只写入当前 `business`，宽频附带Disney+不会进入XE队列。
“不买宽频，只要独立Disney+”是切换，不是BN与XE同时购买，不给旧BN队列追加消息。
分片必须来自本轮消息或待澄清消息，目标业务唯一且包含当前业务；同一片段不能复制到多个业务。
所有分片校验通过后才提交，非法片段或队列溢出不部分写入。

### 需求提取模型

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `changes` | `array[object]` | 最多50个独立需求变更，每项为 `business`、`operation`、`requirement_id`、`text`、`attitude`、`priority`、`evidence`；旧输出可省略 `priority` |
| `question_changes` | `array[object]` | 最多50个咨询变更，每项严格为 `business`、`operation`、`question_id`、`text`、`evidence`；旧输出省略时表示无咨询变更 |
| `clarification` | `string` | 关键需求歧义；只阻止推荐，不撤销独立明确条目 |
| `reason` | `string` | 内部一句依据，不输出推理过程 |
| `context_update` | `object` 或 `null` | 完整新背景及证据；`null` 表示保留旧背景 |

需求文本是自由表达，不读取产品标签白名单；提取模型不接收目录，也不做产品匹配。
每项 `business` 必须等于当前处理业务，缺失或不一致时拒绝该条目，独立合法条目仍可保存。
此检查能拦截显式错归属，不证明模型给出的业务语义一定正确；仍需真实模型验收。
`operation` 为 `add`、`update` 或 `remove`。新增编号为空，由代码生成；更新与撤回引用已有ID。
用户改口应使用一次 `update`，不能拆成先撤回再新增；同一ID本轮出现多次时全部拒绝。
同一维度出现相互冲突的新具体值时，即使用户只回复“1000M”而没有说“改为”，也应结合对话上下文更新原需求；“有什么推荐什么吧”在精确规格缺货时表示放宽该维度，不抹掉其他需求。
每项修改必须有待处理用户消息的非空连续原话依据；不能引用助手问题、转述或地址说明。
咨询使用相同的业务隔离、原话证据、唯一更新和容量校验，新增时空编号由代码生成Q编号。
新维度用新增，同一维度改口才更新，不将多个条件不断合并进旧R编号；重复询问不新增同义咨询。
无待处理用户消息时，需求操作必须为空、背景更新必须为 `null`。
历史若有污染，不自动删除已保存条目；建议新建会话验收，旧状态须先核对后清理，避免误删真实多业务需求。

旧提取输出的 `recommend`、`readiness` 暂兼容接收但忽略，不再影响分支，也不再要求模型生成。
`merge` 只提交明确需求并输出临时 `analyze` 模式；是否追问由产品分析唯一决定。

#### `context_update`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `value` | `string` | 更新后的完整用户背景，不只是增量 |
| `evidence` | `array[string]` | 至少一条本轮原话，必须来自待处理消息 |

`null` 保留旧值；空字符串只有用户明确要求且有证据时才可提交。
更新完整背景时模型须保留未提及的旧事实，明确修改才替换，有歧义先澄清。
证据可核验不代表代码能证明摘要全部语义正确，仍需模型验收。

### 产品分析与初筛模型

`shortlist` 读取当前业务全部可售套餐的自由标签，以及自由需求、逐项原话、背景和问答原话。
当前有效需求以 `requirements` 为准，不能从旧问答恢复已撤回或已被替换的需求。
不使用标签精确匹配计分，不要求预定义标签表；缺少月费的套餐价格仍为未知，不能从编号猜测。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `action` | `string` | `interview`、`clarify`、`recommend` 或 `boundary`；当轮唯一动作 |
| `selected_plan_codes` | `array[string]` | 当前可售范围内最多 5 个唯一编号，按比较价值排序；可为空 |
| `follow_up` | `object` 或 `null` | 追问时包含下列字段，其余动作为 `null` |
| `reason` | `string` | 非空内部决策依据，不是额外套餐事实来源 |

`follow_up` 严格包含：`topic`（稳定主题键）、`information_needed`（一项待确认产品信息）、
`selection_impact`（答案如何改变选择）、`blocking`（布尔值，只有目标不明确或真实冲突可为真，`clarify` 必须为真）。
三个文本字段非空且最多500字符；不输出标签引用或引用ID，不以追问措辞与标签逐字相等为门槛。
提取阶段有关键歧义时只能 `clarify`；没有已保存需求、套餐咨询、背景或实际访谈回答时不能直接推荐。
`questions` 可驱动候选查找，纳入能回答最新问题的合理候选，与原偏好方案比较；疑问不自动成为必须包含某权益的条件。
目标规格不在可售目录中只表示没有精确匹配；只要目录存在相关同业务套餐且用户愿意比较，就选择最接近的候选并披露差距，不把需求误归为boundary或空候选。
`boundary` 仅处理纯越权、无关或诱导请求，使用受控边界说明；混合消息仍可保留合法需求。

`product_decision` 先校验结构、范围、数量、唯一性及追问意图结构；仅推荐动作进入 `rank` 获取详情。
非法输出明确报错，不回退到精确标签计分；空列表不补候选。
详情返回必须与初筛编号和顺序完全一致，不能静默少项或替换套餐。

### 套餐精选模型

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `selected_plan_codes` | `array[string]` | 本轮候选中的精确编号，最多 2 个且不重复 |
| `assessments` | `array[object]` | 每款精选的 `plan_code`、`requirements` 和 `info_answers`；不能遗漏当前任何需求或咨询ID |
| `reason` | `string` | 内部精选依据，非额外产品事实来源 |

每条核对为 `requirement_id`、`status`、`quote`、`explanation`。
`status` 为 `supported`（原文明示符合需求及态度）、`unsupported`（原文明示冲突）或 `unknown`。
支持与不支持必须提供对应候选 `markdown` 支持的依据，可摘录或准确转述；未知可用空依据，不能把未提及当成不支持。
相对偏好未量化时不能擅自判为满足；所有状态都须解释限制与判断边界。
代码校验成员、覆盖范围及依据字段类型/非空，不校验逐字出处或证明语义蕴含关系，仍需真实模型评测。
每款套餐的每个R编号恰好评估一次。旧复合需求有明确冲突时不支持，无冲突但部分未知则未知，不能用部分满足冒充全部满足。
错误区分编号越界、重复和缺失，报告套餐编号和相关ID，供既有有限重生成纠正；不自动删除重复评价。

`info_answers` 每项严格为 `question_id`、`status`、`quote`、`answer`。
每款套餐必须恰好回答每个有效Q编号一次，不能放入R编号；无咨询时可省略该数组以兼容旧输出。
`status` 仅允许 `answered` 或 `unknown`；明确结论必须有对应套餐原文依据，可准确转述，资料未提及时只能未知。
否定结论也要原文依据；SIM旅游数据、家居电话IDD与本地流动通讯不相互代替。
最终回复先回应最新问题，再自然解释方案取舍，不输出内部编号或状态码，不因内部分类反复追问。

最终 `reply` 模型不输出此类 JSON，只输出自然语言。

## 7. 临时对象结构

### `snapshot`

类型为 `object`，键正好是第 3 节的 12 个会话变量名，值保留其原始类型。
用于节点间传递本轮状态，赋值节点按同名键分别写回会话变量。
不是会话变量，不能把它整体当作字符串存回一个总状态字段。
错误处理不静默重置损坏状态。

### 回复 `bundle`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `snapshot` | `object` | 本轮变量快照 |
| `business` | `string` | 当前业务；入口或全局澄清时可为空 |
| `mode` | `string` | 下面列出的内部回复模式 |
| `instruction` | `string` | 需要明确解释的错误、澄清问题或引导 |
| `selected_plans` | `array[object]` | 已校验精选及原文；非推荐或错误时为空 |
| `persist_reply` | `boolean` | 是否在输出后更新对应业务的回复字段 |
| `interview_topic` | `string` | 常规访谈选择的唯一主题，其他模式为空 |

`mode` 为 `request_address`、`interview`、`recommend`、`consult`、`clarify`、`boundary`、`no_plans` 或 `error`。
临时 `process` 表示进入需求提取，`analyze` 表示进入统一产品决策，均不直接交给最终回复模型。

### 标签接口 `bundle`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | `string` | 目录状态；临时还可为 `missing` 或 `consult` |
| `error` | `string` | 明确错误信息，成功为空 |
| `plans_tags` | `array[object]` | 本轮标签结果或复用数组 |
| `request_json` | `string` | 接口请求记录；复用路径为空，实际模拟查询路径为序列化请求 |

同名 `bundle` 在不同聚合链中具有上面两种明确结构，不能混用。

### 候选详情对象

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `plan_code` | `string` | 套餐编号，必须属于当前范围 |
| `tag` | `array[string]` | 原标签 |
| `markdown` | `string` | 内部候选保留完整套餐原文；销售展示对象中的同名字段移除运营商信息但保留套餐事实 |
| `requirement_assessments` | `array[object]` | 仅校验后的精选附带逐需求核对，最终回复须披露不支持与未知项 |
| `info_answers` | `array[object]` | 仅校验后的精选附带逐咨询回答；每个有效Q编号恰好一次，未知不改写成不包含 |

初筛最多 5 款，原文精选最多 2 款；均不为凑数而补历史结果。
候选不再附带精确匹配的分数或未命中标记，偏好缺口由模型结合原文核对。

### 销售展示对象

内部候选与用于 LLM 展示的对象使用相同编号和顺序，不改写 `plan_code`，不改动源文件。
展示对象中的标签和原文移除运营商名称、身份及评价信息；月费、限速、合约、收费和条款保留。
HTML 和 Markdown 链接保留可读文字，避免名称从链接地址再次进入输出。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `display_name` | `string` | 从已处理的原文方案名称取得的中性标题；没有名称时用“服务套餐”，仍保留真实编号 |

`sales_candidates_json` 提供给精选模型；最终的 `selected_plans_json` 也采用此展示对象。
`HKBN SAFE` 展示为“网络安全服务”，设备数、免费期限和后续收费不能因此丢失。
展示不是对原文的完整逐字复制，不可将被移除的名称从缓存、用户消息或常识补回。

## 8. 所有 Code 节点输出

下表字段均为本轮临时变量。`snapshot`、两类 `bundle` 及候选结构见第 7 节。
`*_json` 是供模型或校验器读取的文本，不表示同名会话数组改成了字符串。

| 节点 | 全部输出字段 | 说明 |
| --- | --- | --- |
| `ingress` | `snapshot`、`ok`、`event`、`message`、`error`、`active_business`、`last_reply`、`routing_json` | `ok` 为布尔值，快照为对象，其余字符串；地址事件的 `message` 为空 |
| `input_failure` | `bundle` | 入口错误回复，`persist_reply` 为假 |
| `address_business` | `text` | 固定 BN 的路由 JSON 字符串 |
| `prepare` | `snapshot`、`ok`、`business`、`intent`、`text`、`mode` | 保存路由和待处理需求；失败时区分 `clarify` 与 `error` |
| `scope_reply` | `bundle` | 业务澄清或路由错误，不覆盖其他业务回复 |
| `catalog_context` | `needs_load`、`bundle` | 布尔值决定是否执行标签接口 |
| `fetch_tags` | `bundle` | 标签接口结构，包含本次请求记录 |
| `cached_tags` | `bundle` | 标签复用结构，不请求服务 |
| `catalog` | `snapshot`、`process`、`business`、`mode`、`instruction`、`plans_tags`、`plans_tags_json`、`needs_json` | `process` 布尔值，`plans_tags` 对象数组；目录与需求不再构成白名单依赖 |
| `pre_reply` | `bundle` | 表单、无套餐、目录错误或咨询的回复结构 |
| `merge` | `bundle` | 按条校验自由需求和背景；局部失败不回滚其他明确条目；成功后进入分析 |
| `decision_state` | `snapshot`、`mode`、`instruction`、`needs_json`、`comparison_context_json` | 先保存偏好和背景；`instruction` 与比较上下文显式声明为字符串供模型引用，避免读取汇合对象内未声明的子字段 |
| `product_decision` | `bundle`、`shortlist_json` | 校验统一动作、候选范围及追问结构，不比较标签文字；输出传给详情接口的已校验初筛JSON |
| `skip_analysis` | `bundle` | 前置错误、缺地址或咨询不调用产品决策 |
| `product_state` | `snapshot`、`mode` | 已校验产品动作及对应就绪状态，回复前保存 |
| `rank` | `candidates_json`、`sales_candidates_json`、`request_json`、`error` | 校验初筛后按序取详情；请求记录含 `selected_plan_codes` 和 `shortlist_reason`；错误时两类候选均为 `[]` |
| `validate_selection` | `recommendations_json`、`error` | 两个字符串；编号、需求覆盖或引用校验失败时精选为 `[]` |
| `recommendation` | `bundle` | 合并接口及精选错误，错误时清空产品事实 |
| `basic_reply` | `bundle` | 传递非推荐分支 |
| `reply_context` | `snapshot`、`business`、`mode`、`instruction`、`needs_json`、`selected_plans_json`、`persist_reply`、`plans_tags_json`、`interview_topic`、`user_message` | 最终模型只接收销售展示内容；`instruction` 包含动态追问意图或推荐版式；`plans_tags_json` 固定为 `[]`，避免自行从目录推荐 |
| `address_action` | `action` | 字符串，固定 `request_address`；单独前端动作，不作为聊天文本 |
| `record_reply` | `snapshot` | 完整自然语言回复后更新业务回复、已问主题、问题标记及推荐编号；全局业务澄清则记录实际问题 |

目录已移除 `candidate_tags_json` 与 `allowed_tags_json`，需求保存与标签是否存在无关。
运营商偏好仍不属于可收集需求，也不参与套餐比较。
`catalog.needs_json` 供提取模型读取未经销售改写的真实待处理原话，以便逐字引用；
`decision_state.needs_json` 和 `reply_context.needs_json` 对运营商相关内容作销售视图处理，
但不改写内部保留的原始问答。
`decision_state.comparison_context_json` 是本轮生成的比较视图，不新增会话变量：

| 对象字段 | 含义 |
| --- | --- |
| `latest_user_message` | 当前业务最新一条销售范围内的用户原话；无历史时为空字符串 |
| `previous_recommendations` | 按上轮推荐编号顺序，从当前业务目录取得的编号与中性标签，用于定位比较基准，不代表用户现用服务或权威条款 |
| `unavailable_previous_codes` | 上轮编号中无法在当前目录找到的项目；不补猜规格，不扩大当前可售范围 |

初筛和精选均通过显式字符串变量读取该视图；原文核对仍以本轮对应套餐正文为准。
相对需求应保留比较对象和调整方向，不把“更快”降格为泛泛的“高速”，也不推断用户指定了某个精确档位。

`reply_context.user_message` 是供最终回复读取的销售范围内用户文本，不再直接引用原始 `sys.query`。
`selected_plans_json` 包含通过编号校验后生成的销售展示精选对象。
错误模式还会从提供给 LLM 的需求文本中移除旧回复与旧推荐编号，避免历史产品话术成为兜底事实。

## 9. 其他节点与状态写入

### LLM 输出

四个内部JSON模型的首次生成和失败重生成均在同一个 Loop 中执行，最终继续执行原校验器。默认最多重生成一次，可在构建时通过 `--llm-retries N` 调整，范围为 `0–99`；设为 `0` 时关闭重生成，使用不含 Loop 的原始单次调用。Loop 的 `loop_count` 为 `N + 1`（含首次生成），不超过 Dify 的100轮上限。每个阶段只保留一个原始 LLM 节点和一份业务提示词，仅追加校验反馈。预检结果不写入会话，成功路径不额外调用模型。

| 临时 Code 节点 | 输出 |
| --- | --- |
| `business_validation` | `retry`、`error`、`text` |
| `extract_validation` | `retry`、`error`、`text` |
| `shortlist_validation` | `retry`、`error`、`text` |
| `select_validation` | `retry`、`error`、`text` |
| `business_node_failure` | `text` |
| `extract_node_failure` | `text` |
| `shortlist_node_failure` | `text` |
| `select_node_failure` | `text` |

`retry`是代码是否拒绝本次输出，真实业务澄清不算错误；`error`是校验反馈，`text`保留模型原始输出。这些值仅在本轮使用。
四个内部模型默认启用 `structured_output_enabled`，各自的 `structured_output.schema` 来自
[JSON Schema 协议](../../dify/model_output_schemas.py)，最终 `reply` 保持文本流式。
四个DeepSeek内部模型同时显式设置 `model.completion_params.response_format: json_object`，
这是官方插件支持的原生JSON语法模式，不等于原生JSON Schema；对照版本也保留该参数。
最终自然语言回复不设置 `json_object`，不把流式聊天包装成JSON。
通过 `--structured-output off` 可生成不启用 Schema 的对照版本，默认 `on`。
模型及插件是否原生支持需在目标 Dify 实例核实；Dify 不支持时可能退化为提示词注入，不能宣称为原生受约束解码。
每个模型以 Dify 实际的成功出口 `source` 进入 `*_node_result` 字符串聚合器；失败以 `fail-branch` 进入 `*_node_failure`，
将实际 `error_type` 和 `error_message` 封装成明确失败标记，再由聚合器交给同一校验器。
失败标记在 JSON 解析入口明确拒绝，不成为成功结果或默认推荐。Schema/调用错误同样进入既有有限重试，
耗尽时保留具体错误进入错误回复；即使 `--llm-retries 0` 也保留失败分支，不依赖平台默认中止来通知用户。
错误消息与模型原输出都只是不可信的校验数据。

每个`*_retry_loop`内依次执行原始 LLM、成功/失败聚合、`*_validation`和`*_retry_update`。循环变量`current_text`、`last_error`初始化为空字符串，`retry`初始化为`true`，首次生成正常执行原任务。赋值器将本次输出、错误和是否失败写回循环变量；退出条件仅引用 Loop 自身的`retry`，不引用编辑器中对容器不可见的子节点输出。校验通过时退出，失败时携带最新反馈继续迭代，达到上限后退出。下游仅读取`*_retry_loop.current_text`，业务识别仍通过`business_result`与地址事件的常量结果汇合；地址事件不执行 LLM 循环。
不使用原输出与重试输出拼接，不把预检产生的部分状态作为重试输入；连续失败仍走原错误路径，保留可恢复需求，禁止旧产品兜底。

`business.text`、`extract.text`、`shortlist.text`、`select.text` 是供 Code 解析的内部 JSON 文本；
只有 `reply.text` 是给用户看的自然语言。
`shortlist` 应始终输出 `action`、`selected_plan_codes`、`follow_up`、`reason`。仅当动作明确为 `recommend` 或 `boundary` 且省略 `follow_up` 时，代码将其规范为 `null`；追问和澄清不得省略问题结构，其他缺失或未知字段仍失败。
`selected_plan_codes` 必须逐字复制当前目录完整编号，包含下划线后缀。越界反馈包含精确无效编号和仅当前目录的允许编号，供已有的一次重生成定点纠正；代码不按前缀补号、不静默丢弃错号，也不扩大地址范围。
`select` 应将总体理由放在顶层 `reason`，每个 `assessments` 对象只输出 `plan_code`、`requirements` 和 `info_answers`，逐需求解释使用 `explanation`，逐咨询结论使用 `answer`。
为兼容模型冗余说明，校验器允许逐套餐对象额外携带一个字符串 `reason`，但不保存、不传入最终精选，也不将其作为产品事实。
非文本 `reason`、其他未知字段、缺少必需字段仍报错；编号范围、需求覆盖、状态枚举检查不变。
`quote` 为模型提供的产品事实依据文本，允许准确转述、简繁转换、变更标点、列表格式及行序；
代码不再进行连续子串、逐字或段落匹配，也不声称验证转述事实正确。明确支持/不支持、明确咨询回答仍需非空依据；
unknown 可为空。此修改仅适用于产品事实依据，用户 `evidence` 仍必须逐字来自用户原话。
精选与最终回复模型均须依据对应套餐 `markdown` 核对金额、期限、收费条件与权益归属，冲突时以原文为准并披露，
不以评估文本代替原文。按用户选择不增加独立事实核对节点，事实准确性没有确定性代码保证，需线上评测。
需求或咨询 `text` 中出现受限网络品牌时不保存该条目：纯受限需求进入 `boundary`，合法条目继续处理，
bundle 的可选 `brand_restriction: true` 指示最终回复明确拒绝受限话题并说明含品牌条目未保存，不报告内部错误。
价格比较由提取模型记录为中性金额目标，原话证据可保留品牌名称；不因证据含品牌而拒绝合法需求。
LLM 平台可能附带 usage 等系统信息，本工作流不将这些信息作为业务状态使用。

### 聚合器输出

  "requirements": [],
  "next_requirement_id": 1,
  "questions": [],
  "next_question_id": 1,
  "last_validation_errors": [],
  "user_context": "",
  "pending_messages": [],
  "last_reply": "",
  "interview_pending": false,
  "ready_to_recommend": false,
  "last_recommended_codes": [],
  "interview_rounds": 0,
  "asked_topics": [],
  "last_interview_topic": "",
  "answer_history": []
}
```


| 节点 | 数据来源 | 时点 |
| --- | --- | --- |
| `save_ingress` | `ingress.snapshot` | 新地址旧数据失效后立即写回 |
| `save_scope` | `prepare.snapshot` | 保存业务选择、待处理消息和路由澄清 |
| `save_catalog` | `catalog.snapshot` | 保存标签数组和加载状态 |
| `save_needs` | `decision_state.snapshot` | 保存已校验偏好、背景、用户原话和已答轮数，早于回复生成 |
| `save_decision` | `product_state.snapshot` | 保存已校验产品决策及就绪状态，早于回复生成 |
| `save_reply` | `record_reply.snapshot` | 自然语言输出结束后保存实际问题、已问主题和上下文 |

每个赋值节点按同名键写回第 3 节全部变量，不增加新的会话字段。
路径中未变动的变量原样保留，不把其他业务状态重置为默认值。

## 10. 前端动作与文本流字段

### 动作

Code 节点固定输出：

```json
{"action":"request_address"}
```

普通 Service API 可能隐藏节点输出，因此应用后端使用固定完成事件作为信号：

| SSE 字段 | 约束 |
| --- | --- |
| `event` | `node_finished` |
| `data.node_id` | `address_action`，必须精确匹配节点 ID |
| `data.status` | `succeeded`；失败、开始或重试状态不能当作成功 |
| `data.outputs.action` | 若实例暴露则核对；可能为 `null`，不能必需依赖 |
| `workflow_run_id`、`data.id` | 用于关联本次运行并去重，具体以部署版本事件为准 |

后端向前端转发 `{"action":"request_address"}`，不转发内部快照。
不是前端直接读 Dify Code，也不是从自然语言中寻找关键词。

### 聊天

| 字段 | 含义 |
| --- | --- |
| `reply.text` | 最终自然语言，由 LLM 生成 |
| `answer` 节点配置 | 固定直接引用 `{{#reply.text#}}` |
| SSE `event == "message"` 的 `answer` | 自然语言分片，直接追加显示，不再解析为业务 JSON |
| `message_end` | 文本消息结束，不等于后续赋值全部完成 |
| `workflow_finished` | 用于确认本轮包括回复记录在内的执行结果 |

`answer` 后仍有 `record_reply` 和 `save_reply`，所以后端须等待运行完成再执行下一轮。
字段映射和实际流式时序必须在目标 Dify 实例联调验证，本地替身测试不代表部署已接通。
