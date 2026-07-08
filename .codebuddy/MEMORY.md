# 项目记忆

## 代码注释全部用英文

## SkillsView.vue 详情弹窗底部按钮样式方案（从左到右）

全部按钮统一 `size="small"`，与 `DocumentManage.vue` 标题栏按钮尺寸一致。配色通过 naive-ui CSS 变量内联覆盖实现（`--n-text-color` / `--n-border` 等），无填充背景，适配明暗主题，与 `DocumentManage.vue` 的 `dm-danger-btn` 写法一致。

| 顺序 | 按钮 | 样式 |
|------|------|------|
| 1 | 编辑 | 默认中性 NButton，无填充，`Create` 图标 |
| 2 | 重新上传 | 默认中性，`CloudUpload` 图标 |
| 3 | 重新上传ZIP | 默认中性，包裹在 `NPopover` 内，`CloudUpload` 图标 |
| 4 | 启用/禁用 | 条件着色（边框+字体，无填充）：<br>· 技能启用时显示「禁用」：黄色 `#f59e0b`（hover `#d97706`），`Ban` 图标<br>· 技能禁用时显示「启用」：绿色 `#22c55e`（hover `#16a34a`），`CheckmarkCircle` 图标 |
| 5 | 删除 | 红色边框+字体 `#ef4444`（hover `#dc2626`），`Trash` 图标，外层 `NPopconfirm` 二次确认 |

### 配色规范（边框/字体，无填充背景）
- 红色（危险/删除）：`#ef4444` / hover `#dc2626`
- 黄色（禁用操作）：`#f59e0b` / hover `#d97706`
- 绿色（启用操作）：`#22c55e` / hover `#16a34a`

### 卡片样式
- 禁用技能卡片加 `sk-card-disabled` 类（灰色背景 `var(--color-surface)`，降低不透明度），且名称左侧图标（`Bulb`）仅在 `is_active` 时渲染。
- 卡片右上角 `NSwitch` 控制启用/禁用，用 `.stop` 阻止冒泡，避免误触发卡片详情弹窗。
