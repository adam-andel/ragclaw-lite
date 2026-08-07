# 上下文管理重构 · 分步落地方案

状态：落地中 —— Step 0–4 已提交（`404fe0d`），Step 5 已提交（`7152a69`），Step 6 已实现**未提交**，Step 7 待办
基线 head：`9c1d2e3f4a5b_add_content_token_count`
当前 head：`7152a69`（Step 0–5；Step 6 改动落盘但未 commit）

---

## 0. 决策快照

| 项 | 决定 |
|---|---|
| L1 二次摘要 | **删除**。`summary2_text` 存量数据直接丢 |
| `memory_context` | **纳入** `fit_assembly_context` 预算分配器 |
| 摘要触发 | 70% 异步 / 80% 同步阻塞；回落到 70% 以下恢复异步 |
| 同步档 | **阻塞式 LLM 摘要**，循环至水位回落 |
| 发车条件 | `acc >= MAX` 或 `轮次耗尽 且 acc >= MIN`；两档规则相同 |
| MIN / MAX | 5000 / 20000（资格线 / 发车线），MAX 保持绝对值 |
| recent floor | **不设**。只保留「最新一轮永不折叠」这条隐式不变量 |
| 记忆召回预算 | 10–15%，与文档 RAG 分开计 |
| 游标载体 | **新增 `Message.seq`**，替换位置下标 |
| 超长轮切分 | 优先 Q/A 切开；仍超限则按 `ceil` 均分 |
| `recall_memory` 工具 / TOC | 暂缓 |
| pin 指令框 | 保留在压缩 modal，用户可编辑，Agent 可写入（后续） |

---

## 1. 两处需要修正的算式

### 1.1 分块数公式是笔误

你写的是：

```
chunk_count = ceil(length % 20000)
```

`%` 是取模，不是除法。代入 `length = 45000`：`45000 % 20000 = 5000`，`ceil(5000) = 5000` —— 会切出 5000 块。应为：

```python
chunk_count      = ceil(length / MAX)      # 45000 / 20000 -> 3
max_chunk_length = ceil(length / chunk_count)   # 45000 / 3   -> 15000
```

三块各约 15000，均匀且都在 MAX 以内。你的意图（先定块数再均分）没问题，只是符号写错了。

### 1.2 水位的度量对象必须是 P，不是 window

70% / 80% 不能直接作用于 `llm_context_window`。L0 上限 40% + 记忆 15% + RAG + 工具 schema + 系统前缀 + `max_tokens` 输出预留，相加超过 100%。改为作用于**持久块子预算 P**：

```
total      = context_window - (max_tokens + SUMMARY_SAFETY_MARGIN)   # 现有 _budget()，不动
P          = total - R_prefix - R_tools - R_rag - R_memory
persistent = tokens(history[cursor:]) + tokens(L0)                   # 唯一跨轮累积的量
async_hi   = 0.70 * P
sync_hi    = 0.80 * P
```

连带收益：`SUMMARY_FIXED_OVERHEAD_TOKENS = 16000`（`config_manager.py:387`）这个盲拍常数退休。它存在的唯一理由是「摘要模块看不见 RAG / memory / tools」，有了显式槽位就变成可计算量。`validate_compression_budget()`（`config_manager.py:947`）同步改。

---

## 2. 新发现：超长单条消息会破坏游标不变量

我上一轮说「seq 天然支持半轮位置」——那句话只在**消息级**成立（user 已摘、assistant 未摘）。但你这条规则会切到**消息内部**：

> 如果二者任一依然超 20000 token，那就先确定分几块……

游标是消息级整数，表示不了「这条消息摘了前 15000 字」。两个出路：

**方案 A（推荐）· 超长消息 = 原子摘要单元**
把该消息切成 N 片，**在同一个 segment 内串行摘要 N 次**，产出 N 段 L0，然后游标**一次性跨过整条消息**。

- 游标始终落在消息边界，不变量保住
- 不需要额外字段
- 代价：这一个单元要 N 次 LLM 调用。同步档下阻塞更久，但超长单条消息本身是罕见事件

**方案 B · 加 `summary_msg_offset`**
记录消息内字符偏移。游标变成 `(seq, offset)` 二元组，装配历史时要对该条消息做部分截取。正确但复杂度显著上升，且每个读游标的地方都要处理这个特例。

**我建议 A**，这是本方案里唯一需要你追加拍板的实质决策。下文按 A 书写。

---

## 3. 段规划规则（形式化）

你的规则闭合后没有「等待」分支，全部收敛为四个出口：

```python
def plan_segment(rounds, cursor, MIN, MAX) -> Segment | ArchiveL0 | None:
    acc, end = 0, cursor
    exhausted = True
    for r in rounds_after(cursor, excluding_newest_and_inflight):
        rt = tokens(r)
        if acc + rt > MAX:
            if acc >= MIN:
                exhausted = False
                break                    # 不并入，就此发车
            end, acc = r.end_seq, acc + rt   # 并入 + 轮内切分
            exhausted = False
            break                        # 并入后必然 >= MAX，发车
        acc, end = acc + rt, r.end_seq
        if acc >= MAX:
            exhausted = False
            break

    if acc == 0:            return None            # 游标已到最新轮，无事可做
    if acc >= MAX:          return Segment(cursor, end)
    if exhausted and acc >= MIN:  return Segment(cursor, end)
    if exhausted:           return ArchiveL0()     # 无料可摘，路由到 L0 归档链（零 LLM）
    return None
```

`ArchiveL0` 这条出口对应「未摘要历史总量 < MIN 但水位仍 ≥70%」——说明 L0 自己占了大头。不显式路由的话会陷入「每轮触发、什么都不做」的空转 + 一串 warning。

**异步档与同步档共用同一个规划器与执行器**，区别只有两点：

| | 异步档 (70%) | 同步档 (80%) |
|---|---|---|
| 调度 | `asyncio.create_task` | `await`，在关键路径上 |
| 循环 | 循环至水位 < 70%，每轮重读游标 | 同左，另加迭代上限 |
| 失败 | 记 warning，下轮重试 | **fall through 到 `fit_assembly_context`** |

### 超长轮的单元切分

```
round_tokens > MAX:
  1. 按 Q / A 拆成两个候选单元
  2. 对仍 > MAX 的单元:
       chunk_count = ceil(len / MAX)
       target      = ceil(len / chunk_count)
       贪心装箱到 target，边界优先级：段落(\n\n) > 句子 > 硬切
  3. 该轮产出 N 个 unit，同属一个 segment，游标一次跨过整轮
```

---

## 4. 全局不变量清单

实现时任何一条被破坏都算 bug：

1. **最新一轮永不折叠**（现有 `_token_round_split` 的 `split >= n` 守卫），异步执行时还要额外排除**进行中的那一轮**（规划范围 `seq < 本轮 user 消息 seq`）
2. **L0 追加与游标推进同一事务**。分开写 = 摘要丢了游标却前进（历史永久不可见），或游标没动摘要重复追加
3. **游标 CAS**：`WHERE id=? AND summary_msg_seq=<planned_start>`，影响 0 行即丢弃本次结果
4. **per-conv in-flight 集合**，防止重复规划同一段
5. **后台任务自开 `db_mod.async_session()`**，绝不碰请求 session 上的 `conv` ORM 对象（SQLite 锁）
6. **请求路径永不写 `summary_text` / 游标两列**（identity map 里是旧值，会写回陈旧游标）
7. `fit_assembly_context` **纯瞬态**，不写任何 DB 状态
8. `tool_messages` **成对删**，否则孤儿消息致 LLM 400
9. 任何裁剪循环 `drop = max(1, n * ratio)`，no-op 即降级或 break

---

## 5. 分步落地

### Step 0 · `Message.seq` migration（地基，可独立上线）

新建 `backend/migrations/versions/a1b2c3d4e5f6_add_message_seq.py`，`down_revision = '9c1d2e3f4a5b'`。

```python
def upgrade():
    op.add_column("messages", sa.Column("seq", sa.Integer(), nullable=True))
    op.get_bind().execute(sa.text("""
        WITH ordered AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY conversation_id ORDER BY created_at, rowid
            ) AS rn
            FROM messages
        )
        UPDATE messages
           SET seq = (SELECT rn FROM ordered WHERE ordered.id = messages.id)
    """))
    op.create_index("ix_messages_conv_seq", "messages",
                    ["conversation_id", "seq"], unique=True)
```

`rowid` 作 tiebreaker —— 它就是插入顺序，正是 `created_at` 缺的那一半。SQLite 3.25+ 支持窗口函数，容器内的版本没问题。

配套改动：

- `models/conversation.py` `Message` 加 `seq` 字段；relationship 的 `order_by="Message.created_at"` → `"Message.seq"`
- 写入点 `chat.py:148` / `:837` 补 `seq = SELECT COALESCE(MAX(seq),0)+1 WHERE conversation_id=?`
- 全部 `order_by(Message.created_at)` → `order_by(Message.seq)`：`chat.py:820 / 1522 / 1598 / 1772`
- `conversations` 加 `summary_msg_seq`（新游标），暂与 `summary_msg_count` 并存

**验证**：`docker exec ragclaw-lite alembic upgrade head`，然后查 `SELECT conversation_id, COUNT(*), COUNT(DISTINCT seq) FROM messages GROUP BY 1` 两列应相等；随便开一个老会话看历史顺序不乱。
**回滚**：`downgrade` 删列删索引，`created_at` 排序仍在。

---

### Step 1 · 移除 L1（纯减法，可独立上线）

代码触点：

- `conversation_summary.py`：`_join_summary`、`_estimate` 的 `summary2_text` 形参（:184）、`maybe_archive_and_compact` 的整个 L1 分支（:312-320, :385）、`build_context_with_summary` 三处 `l1` 读取（:508 / :519 / :574）、`compact_conversation`、`MAX_RECOMPACT_ITERS`（:107）
- `chat.py`：`_build_resume_initial_state` 的 `summary2_text` 形参（:548 / :575）、:1109 / :1545 / :1722
- `schemas/chat.py`：:98 / :115 删字段
- `config_manager.py`：`summary_archive_low_pct` 退休（:522 / :552 / :787）；`routers/config.py:39 / :83` 同步
- 前端：`api/chat.ts:108`、`ChatView.vue:721 / :724 / :752`、modal 里 L1 只读展示块

**列删除**用一个独立 migration（`op.drop_column("conversations", "summary2_text")`），不做数据迁移 —— 你已确认直接丢。
`summary_archived_count` **保留**（display-only，前端 `ChatView.vue:1978` 还在用）。

**验证**：老会话打开压缩 modal 不报错、`/conversations/{id}/summary` 返回体无 `summary2_text`、手动压缩按钮仍可用。

---

### Step 2 · 抽出预算中心（承重层入口）

新建 `backend/app/services/context_budget.py`：

```python
@dataclass(frozen=True)
class ContextBudget:
    window: int
    total: int         # window - (max_tokens + SAFETY_MARGIN)
    r_prefix: int
    r_tools: int
    r_rag: int
    r_memory: int
    persistent: int    # == P

    @property
    def async_hi(self) -> int:  ...   # 0.70 * persistent
    @property
    def sync_hi(self) -> int:   ...   # 0.80 * persistent

def compute(*, prefix_tokens: int, tool_tokens: int) -> ContextBudget: ...
```

`r_prefix` / `r_tools` **实测传入**（装配时这两样都在手上，别再估）；`r_rag` / `r_memory` 从 config 百分比算。

实现时先确认一件事：`build_context_with_summary` 在回合开始跑，那个时点 prefix / tool schema 是否已可得。若不可得，退路是启动时缓存一个 `context_reserves()` 提供者 —— 仍远好于 16000 盲拍，因为它按实际模型和已启用工具数计算。

`summary_archive_high_pct`（40%）**保留配置项名，改口径**：从「占 window」改为「占 P」，作为 L0 归档链的触发线。

**验证**：`docker exec` 里直接调 `compute()` 打印各槽位，人工核对 8k / 128k / 1M 三档下 P 为正且合理。

#### ✅ 已完成（落地记录）

实现与方案的三点差异，均为落地时确认的改进：

1. **百分比的分母是 `avail` 而非 `total`**。`avail = total - r_prefix - r_tools`，`r_rag = 25% * avail`、`r_memory = 10% * avail`，于是 `P = 65% * avail`。prefix 与 tools 不可压缩，把增强槽位的份额从「扣掉它们之后」切，`P` 在任何窗口尺寸下都非负且单调——8k 窗口下 prefix+tools 就能吃掉三分之一预算，按 `total` 切会算出负 P。
2. **`r_tools` 走运行时高水位而非常数**。`fit_assembly_context` 本来就在算 `count_tools_tokens(tools)`，把它 `record_tool_tokens()` 回喂预算中心；`default_budget()` 优先用观测到的最大值，冷启动才退回 `FALLBACK_TOOL_TOKENS = 2048`。取 max 不取最新：工具集随 skill 变动，低估会抬高 P 从而推迟压缩，是有害方向。本机实测 3 个 MCP 工具 = 833 token，常数高估 2.5x。
3. **`_overhead()` 保留函数名，改实现**为 `min(default_budget().reserved, _budget() - 512)`。调用点零改动，clamp 保留。

`RAG_BUDGET_PCT` / `MEMORY_BUDGET_PCT` 暂为模块常量，未进 Settings UI —— 留到 Step 7 一起定契约。

**实测槽位**（`prompt_language=en`，`max_tokens=4096`，prefix 实测 1489，tools 实测 833）：

| window | total | rag | mem | **P** | async_hi | sync_hi | l0_cap | `_overhead()` |
|---|---|---|---|---|---|---|---|---|
| 8000 | 3648 | 331 | 132 | **863** | 604 | 690 | 345 | 2785 |
| 32000 | 27648 | 6331 | 2532 | **16463** | 11524 | 13170 | 6585 | 11185 |
| 128000 | 123648 | 30331 | 12132 | **78863** | 55204 | 63090 | 31545 | 44785 |
| 1000000 | 995648 | 248331 | 99332 | **645663** | 451964 | 516530 | 258265 | 349985 |

8k 档 `P=863 < MIN_CONTENT_ROOM=1024`，如期抛 `CONTEXT_WINDOW_LOW_HEADROOM`（旧逻辑同样告警，但理由是 16000 盲拍）。

**两处运行时行为变化**（本步唯一的行为改动，Step 5 会把前者整条路径换掉）：

- `_overhead()` 在 128k 档从 16000 涨到 44785，`_estimate()` 因此更早判定需要折叠历史。语义上这是修正——RAG 30331 + memory 12132 本来就要占位。
- `l0_cap` 在 128k 档从 `40% × 128000 = 51200` 降到 `40% × 78863 = 31545`，归档提前约 38%。这正是「改口径」的目的。

---

### Step 3 · `memory_context` 入 fit

现在 `agent_graph._assemble`（:319）闭包直读 `state.get("memory_context")`，完全不受裁剪管。改动：

- `fit_assembly_context` 签名加 `memory_context: str | None`（现签名见 `conversation_summary.py:776`）
- 回调 `build_messages` **5 参 → 6 参**；改 `agent_graph._assemble`、`agent_nodes` 的 tool_decision 装配点、以及 fit 内部 **5 处**调用
- 新增 `MEM_CHUNK_DELIM`。`_format_memory`（`agent_nodes.py:1170`）现在用 `"\n\n"` 拼接，而召回内容自身就含空行，按它切块会切错
- 新增 `_trim_memory_oldest`，丢相关性最低的尾块、保 ≥1，与 `_trim_rag_oldest`（:718）完全对称；Phase 2 里加「清空 memory」
- Stage 2 单次扫描规划里加 memory 计价循环，与 rag 那段同构

**裁剪顺序改为**：

```
rag → memory → summary → history → tool_payload
```

理由：rag 每轮重检索、丢了可恢复；memory 是 query 驱动的精确增强、同问题下轮照样召回；summary 是唯一提供无条件连续时间线的**覆盖性保底**，L1 删除后它更不能先丢。**先丢增强，后丢保底。**

**验证**：造一个 memory 召回极大的会话（临时把 top-k 调高），确认 fit 会先砍 memory 且不再溢出。

#### ✅ 已完成（落地记录）

- `fit_assembly_context` 签名加 `memory_context: str | None`，返回元组由 6 项扩为 **7 项** `(summary, history, rag, memory, payload, query, dropped)`；两个调用点（`agent_graph.py:345`、`agent_nodes.py:1387`）及它们的 `_assemble` 闭包同步改为 6 参 `(s, h, rag, payload, q, mem)`，解包同步加 `trimmed_mem`。
- `build_messages` 内部 4 处调用统一为 `(cur_s, cur_h, cur_r, cur_p, cur_q, cur_mem)` 顺序（**落地时发现并修掉一处顺序错位 bug**：初版把 `cur_mem` 放第 4 位、`cur_p`/`cur_q` 错位，探针立刻暴露，已改为 payload 第 4 / q 第 5 / mem 第 6）。
- 新增 `MEM_CHUNK_DELIM = "\n\n---\n\n"`（与 `RAG_CHUNK_DELIM` 同值），`_format_memory`（`agent_nodes.py:1171`）改用它拼接；新增 `_trim_memory_oldest` 与 `_trim_rag_oldest` 完全对称（丢尾块、保头块、保 ≥1）。
- 裁剪顺序由旧 `summary → history → rag → tool_payload` 改为方案规定的 `rag → memory → summary → history → tool_payload`，Stage 2 单次扫描规划、Phase 2 无底、Stage 4 兜底循环三处顺序一致同步。
- tool_decision 装配点全程传 `memory_context=None`（该 `_assemble` 不渲染 memory），故只有最终生成路径裁剪 memory——与现状一致。

**探针 `_mem_probe.py` 实测通过**（容器，已删）：
- 含内部空行的 memory chunk 经 `MEM_CHUNK_DELIM` 切块后不被误切，一次 `_trim_memory_oldest` 仅丢尾块；
- memory 为主成本时先被裁、summary/history/tool 全保留（memory 先于 summary）；
- rag 极小时 memory 仍先于 summary 被裁（rag 太小先被裁光，溢出转入 memory）；
- rag 为主成本、tiny summary 时 rag 先被裁、summary 保留（rag 先于 summary）；
- `memory_context=None` 透传无副作用。

---

### Step 4 · `plan_segment` 纯函数

新建（或在 `conversation_summary.py` 内）实现第 3 节的规划器，**先只替换同步路径的 `_token_round_split`**，行为可与旧实现对比验证。

```python
@dataclass
class SummaryUnit:
    text: str
    kind: Literal["rounds", "msg_slice"]

@dataclass
class Segment:
    start_seq: int
    end_seq: int              # exclusive，消息边界对齐
    units: list[SummaryUnit]  # 每个 unit 一次 LLM 调用
    total_tokens: int
```

不落库、不持久化状态，每次重算。MIN / MAX 进 config，给一个随窗口浮动的上界建议 `MAX = clamp(window * 0.15, 8000, 50000)`，`MIN = MAX / 4`（默认值仍是 20000 / 5000）。

**验证**：纯函数，直接 `docker exec ... python -c` 喂构造数据跑边界表 —— 空、单轮、正好 MAX、超长 Q、超长 A、Q 和 A 都超长、耗尽且 acc<MIN。

#### ✅ 已完成（落地记录）

- 新增纯规划器（均在 `conversation_summary.py`，不落库、不持久化状态）：`Round` / `SummaryUnit` / `Segment` / `ArchiveL0` 四个 frozen dataclass；`plan_segment(rounds, cursor, min_tok, max_tok) -> Segment | ArchiveL0 | None`；`_segment_units`（把超限 round 切成 `msg_slice` unit）；`split_long_unit`（超长单元切分，边界优先级 段落 `\n\n` > 句 `.!?`+空白 > 硬切，且**保留句间空白保证 join==原文**）；`_hard_split` / `_char_split`；`_history_to_rounds`（消息列表按 `user` 切分 round）；`plan_segment_sync(history_tail, min_tok, max_tok) -> int|None`（返回尾部相对折叠边界，排除最新 live round）；`segment_thresholds(window)`（`MAX=clamp(win*0.15,8000,50000)`、`MIN=MAX/4`，未知窗口回退 20000/5000）。
- **`plan_segment_sync` 已替换同步路径原 `_token_round_split` 调用**（`build_context_with_summary` 的 `(1)` 压缩块）：当 `plan_segment_sync` 返回 `split` 时折叠 `history[k:k+split]`；若折叠段 `count_messages_tokens > max_tok` 则经 `split_long_unit` 切成原子 unit 串行摘要再拼接，超长单条消息由此走方案 A（原子摘要单元）。
- 删除已死代码：`_token_round_split`（旧溢出比例切分）、`from math import floor`、`HISTORY_COMPRESS_MIN_FRAC/RESERVE_FRAC/RESERVE_MIN` 三个常量（同步路径已不再使用）。

**落地点修正（与方案伪代码的差异）**：终判链新增 `if acc >= min_tok: return Segment(...)` 一档。方案伪代码的 `if exhausted and acc >= MIN` 漏掉了「`acc>=MIN` 但循环因下一轮将溢出 MAX 而提前 break（`exhausted=False`）」这一出口——该情形语义正是「已攒够 MIN，就此发车」，必须返回 Segment 而非 None（探针测试 7 当场暴露）。

**探针 `_plan_probe.py` 实测全绿**（容器，已删）：规划器 8 例（空→None / 单轮→Segment / 多轮累积→Segment / 正好 MAX→Segment / 超长 Q→msg_slice unit / 耗尽且 acc<MIN→ArchiveL0 / 攒够 MIN 遇溢出→Segment / 游标跳过已摘轮）；split 重建等价；同步适配器短尾→None、重尾→split=4（保留 live round）；阈值 clamp 正确。容器重启 `Application startup complete`。

---

### Step 5 · 异步执行器 + 双水位

```python
_INFLIGHT: set[str] = set()          # conversation_id
_BACKGROUND_TASKS: set = set()

def schedule_summary_pass(conv_id):   # fire-and-forget, 同步加 guard
async def _run_async_pass(conv_id):  # 后台任务体，吞异常
async def run_summary_pass(conv_id, *, blocking, emit):  # 同步档入口，自带 guard
async def _run_summary_pass_inner(conv_id, *, blocking, emit):  # 实际规划/摘要/CAS 循环
```

- 后台自开 `db_mod.async_session()`
- L0 追加 + 游标推进同一事务 + CAS（`WHERE id=? AND summary_msg_seq=cursor`，0 行即丢弃）
- 同步档三条护栏：`emit` 发「正在压缩历史」进度步骤（`history_compressing` i18n，zh/en）/ LLM 失败 fall through 到 `fit_assembly_context` / 循环设迭代上限 `MAX_SUMMARY_PASSES=8`，超限即降级机械裁

水位判定接进 `build_context_with_summary`：

```
persistent >= sync_hi   -> await run_summary_pass(blocking=True); 重读游标 k / L0
persistent >= async_hi  -> schedule_summary_pass(conv_id); 本轮回填不折叠，fit 兜底
else                    -> no-op
```

**这一步之后就没有干净的回滚点了**（游标语义已变），Step 0–4 建议先合并稳定几天。

#### ✅ 已完成（落地记录）

- **单执行器双水位**：`run_summary_pass` 同步档 + `schedule_summary_pass` 异步档共用 `_run_summary_pass_inner`。异步档 `asyncio.create_task(_run_async_pass(conv_id))`，吞所有异常；同步档 `await` 在关键路径上，LLM 失败返回 `False` 后由调用方 fall through。
- **游标桥接（关键）**：`history` dict 进 `build_context_with_summary` 时不带 `seq`（chat.py:822/1770 只发 `{role, content, content_token_count}`），故位置下标 `summary_msg_count` 仍是 `recent` 计算的驱动。**CAS 同时推进 `summary_msg_seq` 与 `summary_msg_count` 到同一 `plan.end`**（密集 seq 在落地等价位置下标），直到 Step 7 退役 `summary_msg_count`。
- **每轮重读游标 + 多趟循环**：`_run_summary_pass_inner` 循环 `MAX_SUMMARY_PASSES` 次，每趟 `db.refresh` 重读 `summary_msg_seq`，重算 persistent；`plan_segment(rounds[:-1], cursor, ...)` 排除最新 live 轮；折叠后 `maybe_archive_and_compact` 维护 L0 滚动窗口。`plan is None` / 仅剩 ≤1 轮 → `return False`；`persistent < async_hi` → `return True`（已恢复）。
- **并发双归档危险已排**：异步档发车时本轮回填跳过 stage (2) L0 archive（`scheduled_bg` 标志），因为后台任务才拥有 L0 维护权，本回合再跑会与后台任务重复归档同一批 folds（产出重复 MemoryChunks）。同步档不发车则照常跑 stage (2)。
- **in-flight guard 修正（真实 bug）**：原 `schedule_summary_pass` 在 `asyncio.create_task(run_summary_pass(...))` 后才由任务体内部 `_INFLIGHT.add`，而任务体在事件循环稍后才跑——导致**两个同步 `schedule_summary_pass("x")` 调用都越过 `conv_id in _INFLIGHT` 检查、各建一个任务**（去重失效，违反不变量 #4）。落地改为 **`schedule_summary_pass` 同步 `_INFLIGHT.add`**，任务体改由 `_run_async_pass` 承担（只跑 `_run_summary_pass_inner`，不碰 guard），`done_callback` 清除 guard；`run_summary_pass`（同步档）仍自带 guard。并发双任务问题消失。
- **emit 接通**：`chat.py:1059` 调 `build_context_with_summary(..., emit=emit_agent_step)`；新增 i18n 键 `history_compressing`（zh: "对话历史较长，正在压缩较早的内容以腾出上下文空间，请稍候……"；en: "Conversation history is long; compressing earlier content to free up context space, please wait..."）。
- **`compact_conversation` 同步**：手动压缩路径写 `summary_msg_seq = split`（与 `summary_msg_count` 同值），保持游标一致。

**验证**：容器探针 `_step5_probe.py`（已删）三项全绿——
`[1]` `_round_from_messages` + `plan_segment` 集成 OK（planner 把两轮合折 → `end=5`）；
`[2]` 12 条消息 / `async_hi=54651 sync_hi=62458 P=78073`，`run_summary_pass(blocking)->True`，`summary_msg_seq=7 == summary_msg_count=7`，`summary_text_len=19`，第二趟游标稳定不越进；DB CAS + 多趟循环 + 游标 lockstep OK；
`[3]` in-flight guard + 发车去重 OK（同步加 guard 后两连发车只起一个任务、内层只跑一次）。
容器 `docker restart ragclaw-lite` → `Application startup complete`，无导入错误。

**⚠️ 未提交**：按方案「Step 5 之后无干净回滚点」，本步改动**故意保持未提交**，等 Step 0–5 一起稳定后再决定提交/推送（遵循项目铁律：不自动 commit/push，等用户说「提交」）。

---

### Step 6 · L0 归档链异步化 + 摘要分块器

- L0 超 `summary_archive_high_pct%` × P 触发，每次归档一段，循环至水位以下（无 LLM，成本可忽略）
- 分块器：L0 单段上限 `SUMMARY_MAX_TOKENS = 2000`，`chunk_max_tokens = 800`，约 3 块。**按句/段边界切，不按 token 硬切** —— 摘要是密集叙述，从中间劈开的半句在向量空间里基本是噪声
- 每个子块把该段 heading 带上（`MemoryChunk.heading` 字段现成，归档路径当前空着）
- `archive_memory_essential` 里 BM25 是**全量 rebuild**（取全表喂 `bm25_index.build`），归档频率一高就是 O(n²)。改增量或加节流
- 编辑/重发历史消息后，已归档块按 seq 范围失效，否则库里留旧版本会召回幽灵内容

#### ✅ 已完成（落地记录）

- **L0 分块器** `_chunk_l0_segment`（新增于 `conversation_summary.py`）：把每段折叠段切成 ≤`L0_CHUNK_MAX_TOKENS=800` token 的子块，边界优先级 **句子 → 段落 → 硬切**(最后兜底)。自带 `_L0_SENT_RE = [^。！？!?]*[。！？!?]|[^。！？!?]+` 做句切，**不依赖标点后空白**——原 `_SENT_RE`/Step 4 的 `split_long_unit` 要求标点后有空白，中文句间无空格会静默退化成硬切(既有局限，未动 Step 4 已验证的 helper，保持范围克制)。每段 → 多个 `MemoryChunk` 行。
- **heading 填充** `_segment_heading`：取该段首句(免 LLM)截断到 200 字，作为该 segment 所有子块的 `MemoryChunk.heading`(填了原本恒为空的字段)。
- **BM25 增量更新** `bm25_index.add`（新增）：只对新块跑 jieba 分词、复用既有语料重建 `BM25Okapi`，全程 jieba 工作量 = O(总块数)（旧 `build` 每次重分词全表 → O(n²)）。`archive_memory_essential` 改为：索引已存在 → `add`；冷索引(本进程首次归档/重启未重建)→ 仍 `build` 全表以不丢既有行。`process_pending_memory` 启动用 `build` 不变。按 `id` 去重。
- **归档链循环** `maybe_archive_and_compact` 重写：每轮取**最旧一段**切块归档、缩小 L0、`db.commit()` 原子提交，循环至 L0 < cap 或仅剩最近一段；`L0_MAX_ARCHIVE_SEGMENTS_PER_CALL=32` 上限防异常 L0 拖死请求/后台任务，剩余段下回合继续。归档失败则本回合停止、L0 内联保留(无损、下回合重试)。`summary_archived_count` 按**段数**(非子块数)计。
- 探针 `_step6_probe.py`（已删）四项全绿：`[1]` 长中文段 → 4 子块且均句界对齐、≤cap；`[2]` heading=首句；`[3]` BM25 增量保留旧文档(建+b增+c仍可达)+ 去重；`[4]` 归档循环 → L0 留 1 段、子块 heading/token_count 齐、索引 chunks 正确无重复。容器重启 `Application startup complete`。

#### ⚠️ 遗留（未做，建议 Step 6b）

- **编辑/重发后按 seq 范围失效**：需折叠路径记录每段 seq 范围 + 在 `chat.py` 编辑路径挂钩子。当前 `summary_text` 仅段落拼接、不携带每段 seq 元数据，且编辑路径未读，贸然改会引入半成品死代码。方案第 4 条暂缓，待单独设计。

#### ⚠️ 既有边角（标记，未改）

- `bm25_index.search` 用 `score <= 0` 过滤；`BM25Okapi` 对**单文档**索引 idf 为负(`log(0.5/1.5)<0`)，导致单文档 KB 搜不到任何结果。真实归档时一个 segment 切块成多子块(多文档)很少触发；属 Step 6 范围外且改动有行为风险，未擅自修改，届时单独评估。

---

### Step 7 · 契约与 UI 收口

- `schemas/chat.py`：游标字段改名/改语义，`summary_msg_count` → `summary_msg_seq`
- SSE 事件：`chat.py:991 / 1136 / 1196 / 1356` 四处 `summary_msg_count` 推送同步
- resume 路径 `_build_resume_initial_state`（`chat.py:548`）：**必须从 DB 重读游标**，不能用快照旧值 —— 后台任务可能已推进
- 前端：`ChatView.vue:747` `applySummaryState` 去 L1；modal 加 **pin 指令文本框**（用户可编辑，Agent 写入留接口）；容量条口径改为 persistent / P
- `chat.ts:106-109` 类型同步

---

## 6. 验证手段（不触发 rebuild）

本次重构**不引入任何新依赖**，全程无需 `docker compose build`：

| 手段 | 用途 |
|---|---|
| `docker restart ragclaw-lite` | 后端改动生效（bind 挂载 + `--reload` 常抓不到 9P 改动） |
| `docker exec ragclaw-lite alembic upgrade head` | 跑 migration |
| `docker exec ragclaw-lite python -c "..."` | 纯函数边界表、预算计算核对 |
| 宿主 `py_compile` | 语法快检（UNC 路径可用） |
| 前端 HMR 日志 | 前端改动验证（`node_modules` 在容器卷内，宿主 build 不了） |

---

## 7. 待拍板

1. ~~**超长单条消息的游标语义**~~ —— **已拍板方案 A**（原子单元），落地见 Step 4/5。
2. ~~**L0 占 P 的份额**~~ —— **沿用 40% 但改口径为占 P**（Step 2 `ContextBudget.l0_cap()`），128k 下阈值 51200→31545。
3. ~~**记忆召回预算**~~ —— **定为 10%**（`MEMORY_BUDGET_PCT`，Step 3 落地，`agent_nodes._format_memory` 用 `MEM_CHUNK_DELIM`）。RAG 25% / Memory 10% 暂为模块常量，未进 Settings UI，留到 Step 7 定契约。
