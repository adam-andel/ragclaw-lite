# 文档解析器扩展与插件管理实施计划

## Context

当前 ERAG 后端文档解析架构存在三个痛点：

1. **可扩展性差**：每加一种文档格式需要修改 9 处文件（parser 实现、`ParserService.__init__` 硬编码列表、`supported_types()` 列表、`pyproject.toml`、`dockerfile`、前端 `input.accept`/`typeOptions`/`fileTypeConfig`、单元测试断言）。
2. **维护漂移**：`pyproject.toml` 与 `dockerfile` 各持一份依赖清单已发生漂移；`can_handle` 与 `supported_types()` 是两份独立硬编码列表，容易不同步。
3. **格式覆盖少**：仅支持 pdf/docx/md/txt 4 种，无法满足企业场景下的 Excel/PPT/CSV/HTML/邮件等需求。其中 `.doc` 还是假支持——`python-docx` 实际无法解析老式二进制 `.doc` 文件。

本计划落地三件事：

- **阶段 1（架构重构）**：引入解析器自动注册机制 + 单一依赖源 + `/supported-types` 端点 + 修复 `.doc` bug，让"加新格式 = 加 1 个文件"。
- **阶段 2（预装 10 种格式）**：内置 csv/json/xlsx/pptx/html/eml/rtf/epub/ipynb/msg 共 10 个新 parser，依赖增量约 41MB。
- **阶段 3（启用/禁用插件接口）**：admin 可在 UI 上禁用不需要的内置格式，避免上传校验列表污染。**所有插件仍在镜像内，不开放运行时 `pip install`**——这是企业内部系统，admin-only、全局可用、无多租户隔离。

预期效果：内置 14 种格式（4 现有 + 10 新增），未来加格式只需新建一个 parser 文件 + rebuild 镜像；admin 可见的"插件管理页"提供启用/禁用开关。

---

## 阶段 1：架构重构（基础工程）

### 1.1 扩展 `BaseParser` 抽象基类

**文件**：[backend/app/parsers/base.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/parsers/base.py)

新增数据类 `ParserPluginMeta` 与三个方法：

```python
@dataclass
class ParserPluginMeta:
    name: str                          # unique key, e.g. "excel"
    display_name: str                  # UI label, e.g. "Excel 表格"
    description: str                   # one-line description
    category: str                      # office|data|web|email|ebook|text|notebook
    extensions: list[str]              # MUST match extensions() return value
    enabled_by_default: bool = True
    version: str = "1.0.0"


class BaseParser(ABC):

    @abstractmethod
    def extensions(self) -> list[str]:
        """Return supported extensions without dot, e.g. ['xlsx', 'xls']."""
        ...

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument: ...

    @classmethod
    @abstractmethod
    def plugin_meta(cls) -> ParserPluginMeta: ...

    def can_handle(self, file_type: str) -> bool:
        """Default implementation: subclasses no longer override this."""
        return file_type.lower().lstrip(".") in self.extensions()

    def safe_parse(self, file_path: Path) -> ParsedDocument:
        """Wrap parse() with try/except to convert library exceptions to ValueError."""
        try:
            return self.parse(file_path)
        except Exception as e:
            raise ValueError(
                f"{self.__class__.__name__} 解析失败: {e}"
            ) from e
```

### 1.2 改造 4 个现有 parser

每个文件加 `extensions()` + `plugin_meta()`，删除原 `can_handle()`（继承默认实现）。`parse()` 函数体不动。

**[pdf_parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/parsers/pdf_parser.py)**：`extensions=["pdf"]`，`plugin_meta(name="pdf", display_name="PDF 文档", category="office")`。

**[word_parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/parsers/word_parser.py)**：⚠️ **修复 `.doc` 假支持 bug**——`extensions=["docx"]`（不含 `doc`）。`plugin_meta(name="word", display_name="Word 文档", category="office")`。

**[markdown_parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/parsers/markdown_parser.py)**：`extensions=["md", "markdown"]`，`plugin_meta(name="markdown", display_name="Markdown", category="text")`。

**[txt_parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/parsers/txt_parser.py)**：`extensions=["txt"]`，`plugin_meta(name="txt", display_name="纯文本", category="text")`。

### 1.3 `ParserService` 改自动注册

**文件**：[backend/app/services/parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/services/parser.py)

删除硬编码 `_parsers` 列表，改成 `pkgutil.iter_modules` 扫描 `app.parsers` 包：

```python
import importlib, pkgutil
import app.parsers as parsers_pkg
from app.parsers.base import BaseParser

class ParserService:
    def __init__(self):
        self._parsers: list[BaseParser] = []
        self._discover_internal()

    def _discover_internal(self) -> None:
        for _, module_name, _ in pkgutil.iter_modules(parsers_pkg.__path__):
            module = importlib.import_module(f"app.parsers.{module_name}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type)
                    and issubclass(attr, BaseParser)
                    and attr is not BaseParser
                    and attr.__module__ == module.__name__):
                    self._parsers.append(attr())

    def parse(self, file_path: Path, file_type: str) -> ParsedDocument:
        file_type = file_type.lower().lstrip(".")
        for parser in self._parsers:
            if parser.can_handle(file_type):
                return parser.safe_parse(file_path)
        raise ValueError(f"No parser available for file type: {file_type}")

    def supported_types(self) -> list[str]:
        """Auto-aggregated from all parsers' extensions()."""
        seen, types = set(), []
        for p in self._parsers:
            for ext in p.extensions():
                if ext not in seen:
                    seen.add(ext); types.append(ext)
        return types

    def list_plugins(self) -> list[dict]:
        """For /api/plugins endpoint: returns metadata + extensions for each parser."""
        return [
            {
                "name": p.plugin_meta().name,
                "display_name": p.plugin_meta().display_name,
                "description": p.plugin_meta().description,
                "category": p.plugin_meta().category,
                "extensions": p.extensions(),
                "version": p.plugin_meta().version,
            }
            for p in self._parsers
        ]


parser_service = ParserService()
```

阶段 1 完成时 `_parsers` 全部启用，没有 DB 过滤——阶段 3 再加。

### 1.4 添加 `/supported-types` 端点

**文件**：[backend/app/routers/documents.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/routers/documents.py)

在文件顶部 import 区域附近加：

```python
@router.get("/supported-types")
async def get_supported_types():
    return {"extensions": parser_service.supported_types()}
```

无需权限——只是元数据查询。

### 1.5 前端动态拉取支持格式

**文件**：[frontend/src/api/documents.ts](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/api/documents.ts)

新增 `getSupportedTypes()`：

```ts
export const getSupportedTypes = () =>
  client.get<{ extensions: string[] }>('/documents/supported-types').then(r => r.data)
```

**文件**：[frontend/src/views/DocumentManage.vue](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/views/DocumentManage.vue)

- 删除硬编码 `input.accept = '.pdf,.docx,.md,.txt'`（约第 143 行），改为 onMounted 时拉取并构建：
  ```ts
  const supportedExts = ref<string[]>([])
  onMounted(async () => {
    const res = await getSupportedTypes()
    supportedExts.value = res.extensions
  })
  // triggerFileSelect 内：
  input.accept = supportedExts.value.map(e => `.${e}`).join(',')
  ```
- `typeOptions`（约第 228-232 行）保留为静态基础选项 + 动态追加新格式（或全量动态生成）。
- `fileTypeConfig`（约第 246-255 行）扩充所有 14 种格式的图标/颜色映射。

### 1.6 依赖单一源

**文件**：[dockerfile](file:///d:/AI/Autoclaw/ERAG/erag/dockerfile)

把第 38-46 行的扁平 `pip install` 改成从 `pyproject.toml` 安装：

```dockerfile
COPY backend/pyproject.toml ./backend/
COPY backend/app ./backend/app
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir ./backend
```

**注意**：此改动需测试镜像构建。Dockerfile 中 `python-jose[cryptography]`、`passlib[bcrypt]`、`mem0ai` 必须先加入 `pyproject.toml` 的 dependencies（补齐漂移），否则镜像会缺包。

### 1.7 单元测试调整

**文件**：[backend/tests/unit/test_parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/tests/unit/test_parser.py)

`TestSupportedTypes.test_supported_types_list` 期望值改为：

```python
expected = {"pdf", "docx", "md", "markdown", "txt"}  # 去掉 doc
```

阶段 2 完成后再追加新格式。

---

## 阶段 2：预装 10 种新格式

### 2.1 添加依赖

**文件**：[backend/pyproject.toml](file:///d:/AI/Autoclaw/ERAG/erag/backend/pyproject.toml)

在 `dependencies` 数组追加：

```toml
"openpyxl>=3.1.0",
"python-pptx>=0.6.23",
"beautifulsoup4>=4.12.0",
"lxml>=5.0.0",
"striprtf>=0.0.26",
"ebooklib>=0.18",
"nbformat>=5.10.0",
"extract-msg>=0.50.0",
```

csv/json/eml 用标准库，无需新增依赖。

### 2.2 实现 10 个新 parser

每个文件遵循统一模板（参考 [excel_parser 示例](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/parsers/)）。新建以下文件：

| 文件 | 类名 | extensions | category | 关键库 |
|---|---|---|---|---|
| `csv_parser.py` | `CsvParser` | `["csv"]` | data | 标准库 `csv` |
| `json_parser.py` | `JsonParser` | `["json"]` | data | 标准库 `json` |
| `excel_parser.py` | `ExcelParser` | `["xlsx", "xls"]` | office | `openpyxl` (`read_only=True`) |
| `pptx_parser.py` | `PptxParser` | `["pptx"]` | office | `python-pptx` |
| `html_parser.py` | `HtmlParser` | `["html", "htm"]` | web | `bs4`+`lxml` |
| `email_parser.py` | `EmailParser` | `["eml"]` | email | 标准库 `email` |
| `rtf_parser.py` | `RtfParser` | `["rtf"]` | office | `striprtf` |
| `epub_parser.py` | `EpubParser` | `["epub"]` | ebook | `ebooklib` |
| `notebook_parser.py` | `NotebookParser` | `["ipynb"]` | notebook | `nbformat` |
| `msg_parser.py` | `MsgParser` | `["msg"]` | email | `extract_msg` |

**统一实现要点**：

- 每个类继承 `BaseParser`，实现 `extensions()` / `parse()` / `plugin_meta()`
- `parse()` 返回 `ParsedDocument`，按文档结构生成 `ParsedSection` 列表
  - Excel：每个工作表一个 section，表头作 heading
  - PPT：每张幻灯片一个 section，标题作 heading
  - HTML：按 `<h1>`~`<h6>` 切分，正文段落归到上一个 heading 下
  - EML：主题作 root heading，正文 + 附件名分节
  - JSON：按顶层 key 路径分节
  - CSV：每行作一个 section，或大表分块
  - 其他类似处理
- 大文件保护：Excel 用 `read_only=True`，HTML 限制 `<10MB`，超过则抛 `ValueError`

### 2.3 每个新 parser 配单元测试

**目录**：`backend/tests/unit/`

每个 parser 一个测试文件（或在 `test_parser.py` 内追加测试类），覆盖：
- 基本解析能产生 sections
- 空文件返回空 sections（不抛异常）
- 错误扩展名/损坏文件抛 `ValueError`（通过 `safe_parse()`）
- 每种格式至少 1 个真实样本测试（fixture 文件放 `tests/fixtures/`）

---

## 阶段 3：插件启用/禁用接口

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
    Absence of a row means the plugin is enabled (its default state).
    This keeps the table small and avoids the need to seed on every new parser.
    """
    __tablename__ = "parser_plugin_state"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    disabled_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

**文件**：[backend/app/models/__init__.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/models/__init__.py)

追加 `from app.models.parser_plugin import ParserPluginState` 注册。

### 3.2 迁移脚本

**文件**：[backend/app/database.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/database.py)

在 `_apply_migrations` 内（约第 62 行附近）加新分支：

```python
if "parser_plugin_state" not in applied:
    _migrate_parser_plugin_state(raw)
    raw.execute(
        "INSERT INTO _migrations(name, applied_at) VALUES (?, ?)",
        ("parser_plugin_state", datetime.now(timezone.utc).isoformat()),
    )
```

新增函数：

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

**无需 seed**——表初始为空，所有 parser 默认启用。

### 3.3 Pydantic schemas

**新文件**：`backend/app/schemas/parser_plugin.py`

```python
"""Pydantic schemas for parser plugin management API."""

from datetime import datetime
from pydantic import BaseModel, Field


class PluginInfo(BaseModel):
    """Plugin metadata + current enabled state, returned by GET /api/plugins."""
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

### 3.4 `ParserService` 加启用过滤 + 缓存

**文件**：[backend/app/services/parser.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/services/parser.py)

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
        """Load disabled plugin names from DB. Called on startup + after admin mutation."""
        async with async_session() as db:
            result = await db.execute(
                select(ParserPluginState.name).where(
                    ParserPluginState.disabled == True  # noqa: E712
                )
            )
            self._disabled_names = {row[0] for row in result.all()}
        self._cache_ts = time.time()

    async def _ensure_cache_fresh(self) -> None:
        if time.time() - self._cache_ts > self._CACHE_TTL_SEC:
            await self._refresh_disabled_cache()

    def _is_enabled(self, parser: BaseParser) -> bool:
        return parser.plugin_meta().name not in self._disabled_names

    async def parse(self, file_path: Path, file_type: str) -> ParsedDocument:
        await self._ensure_cache_fresh()
        file_type = file_type.lower().lstrip(".")
        for parser in self._parsers:
            if parser.can_handle(file_type) and self._is_enabled(parser):
                return parser.safe_parse(file_path)
        raise ValueError(f"No parser available for file type: {file_type}")

    async def supported_types(self) -> list[str]:
        """Now async — filtered by enabled state."""
        await self._ensure_cache_fresh()
        seen, types = set(), []
        for p in self._parsers:
            if self._is_enabled(p):
                for ext in p.extensions():
                    if ext not in seen:
                        seen.add(ext); types.append(ext)
        return types

    def list_plugins(self) -> list[dict]:
        """Synchronous metadata list (no DB state). Used by /api/plugins for the base info."""
        return [
            {
                "name": p.plugin_meta().name,
                "display_name": p.plugin_meta().display_name,
                "description": p.plugin_meta().description,
                "category": p.plugin_meta().category,
                "extensions": p.extensions(),
                "version": p.plugin_meta().version,
            }
            for p in self._parsers
        ]
```

**⚠️ 重要：方法签名变更**——`parse()` 和 `supported_types()` 改成 async。这要求同步修改调用方：

- [doc_processor.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/services/doc_processor.py)：`parser_service.parse(...)` 改为 `await parser_service.parse(...)`
- [documents.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/routers/documents.py) 的 `/supported-types` 端点改为 `await parser_service.supported_types()`

**main.py 启动时刷新缓存**：

**文件**：[backend/app/main.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/main.py) lifespan 内（约第 86 行 ToolRegistry 初始化附近）：

```python
# Refresh parser plugin state cache
try:
    await parser_service._refresh_disabled_cache()
    print("Parser plugin state loaded")
except Exception as e:
    print(f"Parser plugin state init warning: {e}")
```

### 3.5 路由

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
    # Load disabled state from DB
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
    """Enable a previously disabled plugin."""
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
    """Disable a plugin. Disabled extensions will be rejected at upload."""
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

**文件**：[backend/app/main.py](file:///d:/AI/Autoclaw/ERAG/erag/backend/app/main.py)

在第 113-124 行的 router 注册区追加：

```python
from app.routers import plugins
app.include_router(plugins.router)
```

### 3.6 前端实现

#### 类型定义

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

#### API client

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

#### 视图组件

**新文件**：`frontend/src/views/PluginsView.vue`

参照 [SkillsView.vue](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/views/SkillsView.vue) 的模板结构（NCard + NDataTable + NModal + useMessage）。表格列：

| 列 | 内容 |
|---|---|
| 插件名 | `display_name` |
| 类别 | `category`（可用 NTag 着色） |
| 支持扩展名 | `extensions.map(e => '.' + e).join(' / ')` |
| 版本 | `version` |
| 状态 | `enabled` 用 NSwitch 显示 |
| 操作 | 启用/禁用按钮 + 禁用原因输入 |

操作：
- NSwitch 切换：调用 enable/disable API
- 禁用时可填 reason（可选，NModal 输入）
- useMessage 提示成功/失败

#### 路由

**文件**：[frontend/src/router.ts](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/router.ts)

在 `/users` 路由附近追加：

```ts
{
  path: '/plugins',
  name: 'plugins',
  component: () => import('@/views/PluginsView.vue'),
  meta: { title: '插件管理', requiresAuth: true, admin: true },
},
```

#### 侧边栏菜单

**文件**：[frontend/src/components/layout/Sidebar.vue](file:///d:/AI/Autoclaw/ERAG/erag/frontend/src/components/layout/Sidebar.vue)

在 `...(auth.isAdmin ? [...] : [])` spread 数组内追加：

```ts
{ label: '插件管理', key: '/plugins', icon: () => h(NIcon, null, { default: () => h(Extensions) }) },
```

需从 `@vicons/ionicons5` 导入 `Extensions` 图标。

---

## 验证方案

### 后端测试

```bash
# 1. 单元测试
cd backend
pytest tests/unit/test_parser.py -v

# 2. 启动后验证端点
uvicorn app.main:app --reload

# 3. 端点检查
curl http://localhost:8000/api/documents/supported-types
# 期望: {"extensions":["pdf","docx","md","markdown","txt","csv","json","xlsx","xls","pptx","html","htm","eml","rtf","epub","ipynb","msg"]}

curl -H "Authorization: Bearer <admin_token>" http://localhost:8000/api/plugins
# 期望: 14 个插件的列表

# 4. 禁用测试
curl -X POST -H "Authorization: Bearer <admin_token>" \
     -H "Content-Type: application/json" \
     -d '{"reason":"测试"}' \
     http://localhost:8000/api/plugins/excel/disable

curl http://localhost:8000/api/documents/supported-types
# 期望: extensions 中不再包含 xlsx/xls

# 5. .doc bug 验证
curl -X POST -F "file=@test.doc" http://localhost:8000/api/documents/upload
# 期望: 400 "不支持的文件类型: .doc"
```

### 前端测试

```bash
cd frontend
pnpm dev
```

1. 用 admin 账号登录，侧边栏出现"插件管理"
2. 进入插件管理页，看到 14 个插件
3. 禁用 Excel 插件，DocumentManage 页面上传文件时 `.xlsx` 不在 accept 列表
4. 重新启用，验证 accept 列表恢复（最多等 60s 缓存或调 refresh-cache）
5. 上传每种新格式的样本文件，验证解析成功并生成 chunks

### 镜像构建验证

```bash
docker compose build erag
docker compose up -d
# 验证容器启动无报错，新依赖已装
docker compose exec erag python -c "import openpyxl, pptx, bs4, lxml, striprtf, ebooklib, nbformat, extract_msg; print('all good')"
```

---

## 关键风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| `ParserService.parse()` 改 async 后遗漏调用方 | 高 | grep 全项目 `parser_service.parse` 确认所有调用点都加 await |
| `supported_types()` 改 async 后前端调用未同步 | 中 | documents.ts 的 `getSupportedTypes` 改成 `await client.get(...)`，端点本身保持 async |
| `.doc` 文件被禁后老数据无法重新解析 | 低 | DB 查询 `SELECT * FROM documents WHERE file_type='doc'`，如有 completed 状态需人工导出内容 |
| 自动注册引入意外类（如测试 stub） | 低 | `attr.__module__ == module.__name__` 过滤；测试放 `tests/` 不放 `parsers/` |
| lxml 系统依赖在 Alpine 缺失 | 低 | 现有 Dockerfile 用 debian 基础镜像，无需改动；如未来换 alpine 需加 `apk add libxml2 libxslt` |
| openpyxl 大文件内存爆 | 中 | `read_only=True` + 上传时限制 xlsx 不超过 50MB（`BaseParser` 加 `max_size_bytes()` 可选方法，本轮先不做） |
| 缓存与 DB 不一致（admin 切换后前端不刷新） | 中 | 切换后立即调 `_refresh_disabled_cache()`；前端 60s 后自动过期；提供 `/refresh-cache` 强制刷新端点 |
| `parser_service` 在模块 import 时实例化，但 DB 未初始化 | 中 | `__init__` 内不查 DB，仅 discover；缓存延迟到首次 `parse()` 或显式 refresh |

---

## 工作量估算

| 阶段 | 内容 | 工作量 |
|---|---|---|
| 1.1 | `BaseParser` 扩展 | 0.5h |
| 1.2 | 4 个现有 parser 改造（含 `.doc` 修复） | 1h |
| 1.3 | `ParserService` 自动注册 | 1h |
| 1.4 | `/supported-types` 端点 | 0.5h |
| 1.5 | 前端动态拉取 | 2h |
| 1.6 | dockerfile 改 `pip install .` + pyproject 补齐 | 2h |
| 1.7 | 单元测试调整 | 0.5h |
| **阶段 1 小计** | | **~7.5h ≈ 1 天** |
| 2.1 | 加依赖 | 0.5h |
| 2.2 | 10 个新 parser 实现 | 12h |
| 2.3 | 10 个 parser 测试 + fixture | 8h |
| **阶段 2 小计** | | **~20h ≈ 2.5 天** |
| 3.1 | DB 模型 | 0.5h |
| 3.2 | 迁移脚本 | 0.5h |
| 3.3 | Pydantic schema | 0.5h |
| 3.4 | `ParserService` 缓存 + async 改造 | 3h |
| 3.5 | plugins router + main.py 注册 | 2h |
| 3.6 | 前端类型/api/view/router/sidebar | 6h |
| 集成测试 + 联调 | | 4h |
| **阶段 3 小计** | | **~16h ≈ 2 天** |
| **总计** | | **~43h ≈ 5-6 天** |

---

## 文件清单总览

### 新建文件（共 18 个）

**后端**：
- `backend/app/parsers/csv_parser.py`
- `backend/app/parsers/json_parser.py`
- `backend/app/parsers/excel_parser.py`
- `backend/app/parsers/pptx_parser.py`
- `backend/app/parsers/html_parser.py`
- `backend/app/parsers/email_parser.py`
- `backend/app/parsers/rtf_parser.py`
- `backend/app/parsers/epub_parser.py`
- `backend/app/parsers/notebook_parser.py`
- `backend/app/parsers/msg_parser.py`
- `backend/app/models/parser_plugin.py`
- `backend/app/schemas/parser_plugin.py`
- `backend/app/routers/plugins.py`

**前端**：
- `frontend/src/api/plugins.ts`
- `frontend/src/views/PluginsView.vue`

**测试 fixture**：
- `backend/tests/fixtures/`（10 个样本文件：sample.csv/json/xlsx/pptx/html/eml/rtf/epub/ipynb/msg）

### 修改文件（共 11 个）

- `backend/app/parsers/base.py`（加 `extensions`/`plugin_meta`/`safe_parse`/`ParserPluginMeta`）
- `backend/app/parsers/pdf_parser.py`（加 extensions + plugin_meta，删 can_handle）
- `backend/app/parsers/word_parser.py`（同上 + 修 `.doc` bug）
- `backend/app/parsers/markdown_parser.py`（同上）
- `backend/app/parsers/txt_parser.py`（同上）
- `backend/app/services/parser.py`（自动注册 + async 改造 + 缓存）
- `backend/app/services/doc_processor.py`（`parse()` 加 await）
- `backend/app/routers/documents.py`（加 `/supported-types` 端点，`supported_types()` 改 await）
- `backend/app/database.py`（加 `_migrate_parser_plugin_state` 迁移）
- `backend/app/main.py`（注册 plugins router + 启动刷新缓存）
- `backend/app/models/__init__.py`（注册 ParserPluginState）
- `backend/pyproject.toml`（加 8 个依赖 + 补齐 jose/passlib/mem0ai 漂移）
- `dockerfile`（改 `pip install .`）
- `backend/tests/unit/test_parser.py`（修 expected + 加新格式断言）
- `frontend/src/api/documents.ts`（加 `getSupportedTypes`）
- `frontend/src/views/DocumentManage.vue`（动态 accept + typeOptions + fileTypeConfig 扩充）
- `frontend/src/types/index.ts`（加 Plugin 类型块）
- `frontend/src/router.ts`（加 `/plugins` 路由）
- `frontend/src/components/layout/Sidebar.vue`（加菜单项）

---

## 实施顺序建议

1. **先做阶段 1.1-1.3 + 1.7**：base.py + 4 个现有 parser 改造 + parser.py 自动注册 + 测试调整。本地跑通单元测试，确认无回归。
2. **再做阶段 1.6**：dockerfile 改造。本地 build 一次镜像确认依赖装齐。
3. **然后阶段 2**：10 个新 parser + 依赖 + 测试。每写 2-3 个就 build 镜像跑一次集成测试。
4. **再做阶段 1.4-1.5**：`/supported-types` 端点 + 前端动态拉取。这步相对独立，可在阶段 2 之前或之后做。
5. **最后阶段 3**：DB 模型 + 迁移 + service 改造 + 路由 + 前端管理页。

阶段 3 是改造最深的（async 改造影响调用方），建议放在最后，确保阶段 1+2 已稳定。
