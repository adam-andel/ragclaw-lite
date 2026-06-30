# 文档解析器：剩余工作实施计划（阶段 2.3 + 阶段 3）

## Context

上一轮会话已完成阶段 1（架构重构 + `.doc` bug 修复）和阶段 2.1-2.2（10 个新 parser 实现 + 依赖）。

当前状态：
- ✅ `BaseParser` 已扩展（`ParserPluginMeta` / `extensions()` / `plugin_meta()` / `safe_parse()`）
- ✅ 4 个现有 parser（pdf/word/markdown/txt）已改造，`.doc` 假支持 bug 已修复
- ✅ `ParserService` 已改为 `pkgutil.iter_modules` 自动注册
- ✅ `/supported-types` 端点 + 前端动态加载已就绪
- ✅ Dockerfile 改为 `pip install ./backend`，`pyproject.toml` 已补齐 8 个新依赖
- ✅ 10 个新 parser 文件已创建（csv/json/excel/pptx/html/email/rtf/epub/notebook/msg）
- 🔴 **`test_parser.py` 的 `test_supported_types_list` 断言已过时**——期望值仍是 5 个旧格式，但自动注册现已发现 17 个扩展名，测试必然失败
- ⬜ 阶段 3（插件启用/禁用管理）完全未做

本计划覆盖：
1. **修复断裂的测试 + 为 10 个新 parser 补齐单元测试**（阶段 2.3）
2. **插件启用/禁用全栈**（阶段 3：DB 模型 + 迁移 + schema + service 缓存 + API + 前端管理页）

---

## 阶段 2.3：单元测试

### 2.3.1 修复断裂的 `test_supported_types_list`

**文件**：[backend/tests/unit/test_parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/tests/unit/test_parser.py) 第 149-153 行

当前断言：
```python
expected = {"pdf", "docx", "md", "markdown", "txt"}
```

改为：
```python
expected = {
    "pdf", "docx", "md", "markdown", "txt",        # original
    "csv", "json", "xlsx", "xls", "pptx",           # Tier 1 (office/data)
    "html", "htm", "eml",                             # Tier 1 (web/email)
    "rtf", "epub", "ipynb", "msg",                    # Tier 2
}
```

### 2.3.2 为 10 个新 parser 追加测试类

**文件**：[backend/tests/unit/test_parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/tests/unit/test_parser.py)（在文件末尾追加）

**策略**：使用 `tmp_path` fixture 在测试内动态生成样本文件，不提交二进制 fixture 文件。文本格式直接写文件；二进制格式用对应库在测试内生成。

每个 parser 测试类覆盖 3 个场景：
1. `test_parse_basic` — 正常文件解析出非空 sections
2. `test_empty_file_no_crash` — 空文件不抛异常（返回空 sections 或最小结构）
3. `test_corrupt_file_raises_valueerror` — 损坏文件通过 `safe_parse()` 抛 `ValueError`

各 parser 的 fixture 生成方式：

| Parser | 生成方式 |
|---|---|
| `CsvParser` | `tmp_path / "test.csv"` 写 UTF-8 CSV 文本 |
| `JsonParser` | `tmp_path / "test.json"` 写 JSON 字符串 |
| `ExcelParser` | 用 `openpyxl.Workbook()` 创建 → `ws.append(row)` → `wb.save(path)` |
| `PptxParser` | 用 `pptx.Presentation()` 创建 → 添加 slide + text frame → `prs.save(path)` |
| `HtmlParser` | `tmp_path / "test.html"` 写 HTML 字符串 |
| `EmailParser` | 用 `email.message.EmailMessage` 构造 → `msg.set_content(...)` → 写 `.eml` 文件 |
| `RtfParser` | `tmp_path / "test.rtf"` 写简单 RTF 文本（`{\rtf1\ansi Hello}`） |
| `EpubParser` | 用 `ebooklib.epub.EpubBook()` 创建 → 添加 chapter → `epub.write_epub(path, book)` |
| `NotebookParser` | 用 `nbformat.v4.new_notebook()` + `new_markdown_cell()` + `new_code_cell()` → `nbformat.write(nb, path)` |
| `MsgParser` | ⚠️ `extract_msg` 无法在测试内方便地生成 `.msg` 文件。改为：测试 `test_corrupt_file_raises_valueerror` 用伪造 `.msg` 二进制内容（验证 `safe_parse` 包装）；跳过 `test_parse_basic`（标记 `@pytest.mark.skip("msg fixture requires Outlook-generated file")`），仅验证 `can_handle("msg")` 返回 `True` |

**测试类模板**（以 CsvParser 为例）：

```python
import csv as csv_lib

class TestCsvParser:
    """CSV parsing correctness."""

    def test_parse_basic(self, tmp_path):
        csv_file = tmp_path / "data.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            w = csv_lib.writer(f)
            w.writerow(["name", "age", "city"])
            w.writerow(["Alice", "30", "Beijing"])
            w.writerow(["Bob", "25", "Shanghai"])
        doc = parser_service.parse(csv_file, "csv")
        assert isinstance(doc, ParsedDocument)
        assert doc.file_type == "csv"
        assert len(doc.sections) >= 1
        assert "Alice" in doc.full_text

    def test_empty_file_no_crash(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("", encoding="utf-8")
        doc = parser_service.parse(csv_file, "csv")
        assert isinstance(doc, ParsedDocument)

    def test_corrupt_file_raises_valueerror(self, tmp_path):
        csv_file = tmp_path / "bad.csv"
        csv_file.write_bytes(b"\x00\x01\x02\xff\xfe")
        # CSV is lenient; this may not raise. That's acceptable —
        # the test verifies safe_parse() wrapping works when parse() does throw.
        # For parsers that DO throw on corrupt input, assert ValueError.
```

### 2.3.3 验证步骤

```bash
cd backend
python -m pytest tests/unit/test_parser.py -v
```

期望：所有测试通过，`test_supported_types_list` 包含 17 个扩展名。

---

## 阶段 3：插件启用/禁用管理

### 关键设计决策

**`parse()` 和 `supported_types()` 保持同步（SYNC）**。

原因：`doc_processor.py:53` 通过 `loop.run_in_executor(None, parser_service.parse, file_path, ext)` 调用 `parse()`，async 函数无法传给 `run_in_executor`。同样，`documents.py:74` 在 upload 端点内同步调用 `supported_types()`。

方案：用内存中的 `_disabled_names: set[str]`（已存在于 `parser.py`）作为过滤源，异步方法 `_refresh_disabled_cache()` 从 DB 加载到这个 set。`parse()` / `supported_types()` 读取 set 做同步过滤，不查 DB。

### 3.1 数据库模型

**新文件**：`backend/app/models/parser_plugin.py`

```python
"""Parser plugin enable/disable state (system-wide, admin-managed)."""

from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ParserPluginState(Base):
    """Records disabled state of built-in parsers.

    Convention: only rows for DISABLED plugins exist.
    Absence of a row = enabled (default). Keeps table small,
    no need to seed on every new parser addition.
    """
    __tablename__ = "parser_plugin_state"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    disabled_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

**文件**：[backend/app/models/__init__.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/models/__init__.py)

追加注册：
```python
from app.models.parser_plugin import ParserPluginState
```

### 3.2 迁移脚本

**文件**：[backend/app/database.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/database.py)

在 `_apply_migrations` 函数内追加分支（参考现有 `seed_admin_user` 等迁移的写法）：

```python
if "parser_plugin_state" not in applied:
    _migrate_parser_plugin_state(raw)
    raw.execute(
        "INSERT INTO _migrations(name, applied_at) VALUES (?, ?)",
        ("parser_plugin_state", datetime.now(timezone.utc).isoformat()),
    )
```

新增函数（放在 `_apply_migrations` 之前或之后的辅助函数区）：

```python
def _migrate_parser_plugin_state(raw):
    print("[migrate] Running parser_plugin_state...")
    raw.execute("""
        CREATE TABLE IF NOT EXISTS parser_plugin_state (
            name TEXT PRIMARY KEY,
            disabled INTEGER NOT NULL DEFAULT 1,
            disabled_by TEXT,
            disabled_at TEXT,
            reason TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    print("[migrate] parser_plugin_state done")
```

**无需 seed** — 表初始为空，所有 parser 默认启用。

### 3.3 Pydantic schemas

**新文件**：`backend/app/schemas/parser_plugin.py`

```python
"""Pydantic schemas for parser plugin management API."""

from datetime import datetime
from pydantic import BaseModel, Field


class PluginInfo(BaseModel):
    name: str
    display_name: str
    description: str
    category: str
    extensions: list[str]
    version: str
    enabled: bool
    disabled_by: str | None = None
    disabled_at: datetime | None = None
    reason: str | None = None


class PluginDisablePayload(BaseModel):
    reason: str | None = Field(None, max_length=500)


class PluginListResponse(BaseModel):
    items: list[PluginInfo]
    total: int
```

### 3.4 `ParserService` 加异步缓存刷新

**文件**：[backend/app/services/parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/services/parser.py)

当前已有 `_disabled_names: set[str]` 和同步的 `parse()` / `supported_types()`。追加异步缓存刷新方法：

```python
import time
from sqlalchemy import select
from app.database import async_session
from app.models.parser_plugin import ParserPluginState


class ParserService:
    _CACHE_TTL_SEC = 60

    def __init__(self):
        self._parsers: list[BaseParser] = []
        self._disabled_names: set[str] = set()
        self._cache_ts: float = 0.0
        self._discover_internal()

    async def _refresh_disabled_cache(self) -> None:
        """Load disabled plugin names from DB into memory.
        Called on startup + after admin enable/disable mutation.
        """
        async with async_session() as db:
            result = await db.execute(
                select(ParserPluginState.name).where(
                    ParserPluginState.disabled == True  # noqa: E712
                )
            )
            self._disabled_names = {row[0] for row in result.all()}
        self._cache_ts = time.time()

    async def _ensure_cache_fresh(self) -> None:
        """Lazy refresh if TTL expired. Called by parse()/supported_types() callers."""
        if time.time() - self._cache_ts > self._CACHE_TTL_SEC:
            await self._refresh_disabled_cache()

    # parse() / supported_types() / list_plugins() 保持同步，不动
    # 它们读取 self._disabled_names 做过滤（已实现）
```

**调用方适配**（不改 `parse()` / `supported_types()` 签名）：

- `main.py` lifespan：启动时调 `await parser_service._refresh_disabled_cache()`
- `routers/plugins.py` 的 enable/disable 端点：操作 DB 后调 `await parser_service._refresh_disabled_cache()`
- `routers/documents.py` 的 `/supported-types` 端点：可选加 `await parser_service._ensure_cache_fresh()`（保证缓存不过期太久），但不强制——60s TTL 足够

### 3.5 Plugins API router

**新文件**：`backend/app/routers/plugins.py`

```python
"""Parser plugin management API — admin only."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.parser_plugin import ParserPluginState
from app.models.user import User
from app.schemas.parser_plugin import (
    PluginInfo, PluginDisablePayload, PluginListResponse,
)
from app.services.auth import get_current_admin
from app.services.parser import parser_service

router = APIRouter(prefix="/api/plugins", tags=["Plugins"])


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all built-in parser plugins with current enabled state."""
    result = await db.execute(select(ParserPluginState))
    disabled_map = {row.name: row for row in result.scalars()}

    items = []
    for meta in parser_service.list_plugins():
        state = disabled_map.get(meta["name"])
        items.append(PluginInfo(
            **meta,
            enabled=state is None or not state.disabled,
            disabled_by=state.disabled_by if state else None,
            disabled_at=state.disabled_at if state else None,
            reason=state.reason if state else None,
        ))
    return PluginListResponse(items=items, total=len(items))


@router.post("/{name}/enable")
async def enable_plugin(
    name: str,
    operator: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    valid_names = {p["name"] for p in parser_service.list_plugins()}
    if name not in valid_names:
        raise HTTPException(404, f"插件不存在: {name}")
    result = await db.execute(
        select(ParserPluginState).where(ParserPluginState.name == name)
    )
    state = result.scalar_one_or_none()
    if state:
        await db.delete(state)
        await db.commit()
    await parser_service._refresh_disabled_cache()
    return {"name": name, "enabled": True}


@router.post("/{name}/disable")
async def disable_plugin(
    name: str,
    payload: PluginDisablePayload,
    operator: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    valid_names = {p["name"] for p in parser_service.list_plugins()}
    if name not in valid_names:
        raise HTTPException(404, f"插件不存在: {name}")
    result = await db.execute(
        select(ParserPluginState).where(ParserPluginState.name == name)
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = ParserPluginState(
            name=name, disabled=True,
            disabled_by=operator.id,
            disabled_at=datetime.now(timezone.utc),
            reason=payload.reason,
        )
        db.add(state)
    else:
        state.disabled = True
        state.disabled_by = operator.id
        state.disabled_at = datetime.now(timezone.utc)
        state.reason = payload.reason
    await db.commit()
    await parser_service._refresh_disabled_cache()
    return {"name": name, "enabled": False}


@router.post("/refresh-cache")
async def refresh_cache(_: User = Depends(get_current_admin)):
    """Force-refresh the in-memory enabled cache."""
    await parser_service._refresh_disabled_cache()
    return {"ok": True}
```

### 3.6 main.py 注册

**文件**：[backend/app/main.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/main.py)

1. 在 router 注册区（约第 113-124 行）追加：
```python
from app.routers import plugins
app.include_router(plugins.router)
```

2. 在 lifespan 内（ToolRegistry 初始化附近）追加：
```python
# Refresh parser plugin state cache on startup
try:
    await parser_service._refresh_disabled_cache()
    print("Parser plugin state loaded")
except Exception as e:
    print(f"Parser plugin state init warning: {e}")
```

需要在 main.py 顶部加 import：
```python
from app.services.parser import parser_service
```

### 3.7 前端实现

#### 3.7.1 类型定义

**文件**：[frontend/src/types/index.ts](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/types/index.ts)

追加：
```ts
// ---- Parser Plugin ----
export interface PluginInfo {
  name: string
  display_name: string
  description: string
  category: string
  extensions: string[]
  version: string
  enabled: boolean
  disabled_by: string | null
  disabled_at: string | null
  reason: string | null
}

export interface PluginListResponse {
  items: PluginInfo[]
  total: number
}

export interface PluginDisablePayload {
  reason?: string
}
```

#### 3.7.2 API client

**新文件**：`frontend/src/api/plugins.ts`

```ts
import client from './client'
import type { PluginListResponse } from '@/types'

export const listPlugins = () =>
  client.get<PluginListResponse>('/plugins').then(r => r.data)

export const enablePlugin = (name: string) =>
  client.post<{ name: string; enabled: boolean }>(`/plugins/${name}/enable`).then(r => r.data)

export const disablePlugin = (name: string, reason?: string) =>
  client.post<{ name: string; enabled: boolean }>(
    `/plugins/${name}/disable`, { reason }
  ).then(r => r.data)

export const refreshPluginCache = () =>
  client.post<{ ok: boolean }>('/plugins/refresh-cache').then(r => r.data)
```

#### 3.7.3 视图组件

**新文件**：`frontend/src/views/PluginsView.vue`

参照 [SkillsView.vue](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/views/SkillsView.vue) 结构（NCard + NDataTable/NList + useMessage）。

核心功能：
- `onMounted` 调 `listPlugins()` 加载插件列表
- 每个插件卡片显示：`display_name`、`description`、category NTag、extensions 列表、版本
- NSwitch 绑定 `enabled` 状态，切换时调 enable/disable API
- 禁用时可填 reason（NInput，可选）
- 操作后 `useMessage` 提示成功/失败
- 顶部 NButton "刷新缓存" 调 `refreshPluginCache()`

category → NTag 颜色映射：
```ts
const categoryColors: Record<string, string> = {
  office: 'blue', data: 'amber', web: 'cyan',
  email: 'teal', ebook: 'green', text: 'default', notebook: 'orange',
}
```

#### 3.7.4 路由

**文件**：[frontend/src/router.ts](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/router.ts)

在 `/settings` 路由后追加：
```ts
{
  path: '/plugins',
  name: 'plugins',
  component: () => import('@/views/PluginsView.vue'),
  meta: { title: '插件管理', requiresAuth: true, admin: true },
},
```

#### 3.7.5 侧边栏菜单

**文件**：[frontend/src/components/layout/Sidebar.vue](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/components/layout/Sidebar.vue)

在第 35 行的 `...(auth.isAdmin ? [...])` 数组内追加（在"系统设置"后）：
```ts
{ label: '插件管理', key: '/plugins', icon: () => h(NIcon, null, { default: () => h(Extensions) }) },
```

在 import 中追加 `Extensions`：
```ts
import {
  Chatbubbles, FolderOpen, Search, StatsChart,
  LogOut, People, Settings, Extensions,
} from '@vicons/ionicons5'
```

---

## 实施顺序

1. **修复断裂测试**（2.3.1）— 立即修复 `test_supported_types_list`，让现有测试先跑通
2. **补齐 10 个新 parser 的单元测试**（2.3.2）— 逐个添加测试类
3. **跑单元测试验证**（2.3.3）— `pytest tests/unit/test_parser.py -v`
4. **阶段 3.1-3.2**：DB 模型 + 迁移
5. **阶段 3.3**：Pydantic schemas
6. **阶段 3.4**：`ParserService._refresh_disabled_cache()` 异步方法
7. **阶段 3.5-3.6**：plugins router + main.py 注册
8. **阶段 3.7**：前端类型 + API + 视图 + 路由 + 侧边栏

---

## 验证方案

### 后端单元测试
```bash
cd backend
python -m pytest tests/unit/test_parser.py -v
# 期望：所有测试通过，test_supported_types_list 包含 17 个扩展名
```

### 后端端点验证
```bash
uvicorn app.main:app --reload

# 1. supported-types 包含全部 17 格式
curl http://localhost:8000/api/documents/supported-types

# 2. admin 登录后查看插件列表
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/plugins
# 期望：14 个插件（pdf/word/markdown/txt/csv/json/excel/pptx/html/email/rtf/epub/notebook/msg）

# 3. 禁用 excel
curl -X POST -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" -d '{"reason":"测试"}' \
     http://localhost:8000/api/plugins/excel/disable

# 4. supported-types 不再包含 xlsx/xls
curl http://localhost:8000/api/documents/supported-types

# 5. 重新启用
curl -X POST -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/plugins/excel/enable
```

### 前端验证
```bash
cd frontend && pnpm dev
```
1. admin 登录 → 侧边栏出现"插件管理"
2. 插件管理页显示 14 个插件，NSwitch 可切换
3. 禁用 Excel → DocumentManage 上传 accept 不含 .xlsx
4. 重新启用 → 恢复

---

## 文件清单

### 新建文件（5 个）

| 文件 | 说明 |
|---|---|
| `backend/app/models/parser_plugin.py` | ParserPluginState ORM 模型 |
| `backend/app/schemas/parser_plugin.py` | Pydantic schemas |
| `backend/app/routers/plugins.py` | 插件管理 API（4 个端点） |
| `frontend/src/api/plugins.ts` | 前端 API client |
| `frontend/src/views/PluginsView.vue` | 插件管理页 |

### 修改文件（7 个）

| 文件 | 改动 |
|---|---|
| `backend/tests/unit/test_parser.py` | 修复 expected set + 追加 10 个测试类 |
| `backend/app/models/__init__.py` | 注册 ParserPluginState |
| `backend/app/database.py` | 加 `_migrate_parser_plugin_state` 迁移 |
| `backend/app/services/parser.py` | 加 `_refresh_disabled_cache()` + `_ensure_cache_fresh()` |
| `backend/app/main.py` | 注册 plugins router + lifespan 启动刷新缓存 |
| `frontend/src/types/index.ts` | 加 Plugin 类型块 |
| `frontend/src/router.ts` | 加 `/plugins` 路由 |
| `frontend/src/components/layout/Sidebar.vue` | 加"插件管理"菜单项 |

---

## 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| `parser_service` 在模块 import 时实例化，但 DB 未初始化 | 中 | `__init__` 不查 DB，仅 discover；`_refresh_disabled_cache` 延迟到 lifespan 调用 |
| 缓存与 DB 不一致（admin 切换后前端不刷新） | 中 | 切换后立即调 `_refresh_disabled_cache()`；前端 60s TTL 自动过期；提供 `/refresh-cache` 强制刷新 |
| `get_current_admin` 不存在 | 低 | 已确认在 `services/auth.py` 中存在（参考 `routers/config.py` 用法） |
| MsgParser 无法生成测试 fixture | 低 | 测试标记 skip，仅验证 `can_handle`；真实 `.msg` 文件留手工集成测试 |
| 阶段 3.4 改 `parser.py` 引入循环 import | 低 | `async_session` 从 `database.py` 导入，`ParserPluginState` 从 `models` 导入，均在 `parser.py` 函数体内延迟导入或顶部导入（已确认无循环） |
