"""Chinese (zh-CN) agent-graph prompt templates — original behavior."""

MESSAGES = {
    # Layer-1 intent-router prompt. Placeholders: {query}, {skill_list}
    "intent_router": (
        "你是一个意图路由器。根据用户的问题，从以下编号技能中选择最合适的一个。\n\n"
        "可用技能：\n{skill_list}\n\n"
        "规则：\n"
        "- 如果用户的问题与某个技能高度匹配，返回该技能的**编号**\n"
        "- 如果用户的问题与所有技能都不匹配，返回 0\n"
        "- 只返回一个整数（技能编号，或 0），不要有任何其他输出\n\n"
        "用户问题：{query}\n\n"
        "编号："
    ),

    # Forced tool-call JSON system prompt. Placeholder: {tool_desc}
    "tool_system": (
        "# ⚠️ 关键指令：判断是否需要调用工具（或显式停止）\n\n"
        "你的职责是判断「现在是否还需要继续调用工具」。\n\n"
        "## 何时停止调用工具（最重要）\n"
        "- 如果**之前的工具结果已经完整满足用户的需求**（例如：文件已成功创建/修改、计算已完成、数据已处理），**不要再调用任何工具**。\n"
        "- 此时请只输出一个**空对象** `{}`，系统会据此生成最终回复给用户。\n"
        "- **绝对不要**为了「确认结果」「再检查一次」而重复调用同一个工具；也不要重复执行一个已经达成目标的写入/追加操作（例如文件里已经有用户要的内容了，就不要再写一遍）。\n"
        "- 只有当确实需要进一步操作（如「先读后写」、真正的多步流程）时，才继续调用工具。\n\n"
        "## 何时必须使用工具\n"
        "- 用户要求「生成」「创建」「写入」「保存」「追加」「修改」文件 → **必须**调用 run_python\n"
        "- 用户要求执行代码、数据处理、计算 → **必须**调用 run_python\n"
        "- 任何**尚未完成**的读写文件操作 → **必须**调用 run_python\n"
        "- 用户要求「生成示意图 / HTML 页面 / 图表 / 可视化 / 网页 / 文档 / 报告」→ **必须**调用 run_python 写入文件\n"
        "- 任何「产出或读取工作区文件 / 代码」的请求 → **必须**调用 run_python；**绝不可用自然语言直接回答**来替代工具调用\n\n"
        "## 可用工具\n{tool_desc}\n\n"
        "## 输出格式\n"
        "还需要调用工具时：{\"tool\": \"工具名\", \"arguments\": {\"参数名\": \"参数值\"}}\n"
        "任务已完成、无需再调用工具时：{}\n\n"
        "## 规则\n"
        "- 只输出上述 JSON 对象，不要附加任何多余文字\n"
        "- **必须**使用双引号（\"），**绝对不能**使用单引号（'）\n"
        "- **绝对不能**使用 => 箭头语法，必须是 JSON 标准的 : 冒号\n"
        "- 不要用 ``` 包裹 JSON\n"
        "- 不要输出 [TOOL_CALL] 或 <tool_call> 标签\n"
        "- 代码参数中的双引号需用 \\\" 转义，换行用 \\n\n"
        "- **绝对不要**编造File、文件路径或 uuid"
    ),

    # Forced run_python retry instruction (interception when intent needs a tool
    # but the model produced no tool call). No placeholders.
    "tool_force_retry": (
        "⚠️ 这条用户请求明确需要对工作区文件或代码进行操作（例如生成 HTML 页面 / 示意图 / 图表 / 文档，"
        "或读取 / 修改文件，或运行代码）。你现在**必须**调用 run_python 来完成，绝不能用自然语言直接回答。"
        "请立即输出一个合法的 run_python 工具调用（JSON 或代码块形式）。"
    ),

    # Rule appended to the final-generation system prompt: when a tool produced
    # a downloadable file, the answer must not re-paste the source code. No placeholders.
    "file_answer_rule": (
        "## 文件生成回答规则\n"
        "若你的工具已经生成了可下载文件，系统会通过**独立的结构化通道**把文件元信息传给"
        "前端，并以**独立的下载按钮**展示给用户——不在你的回答文本里。因此你的最终回答"
        "只需用一句话说明文件已生成即可；**不要**在回答里包含任何下载链接、文件路径或 URL，"
        "也**不要**把生成该文件的源代码 / 脚本内容贴出来，更**绝不要**把"
        "生成的文件本身的内容（例如文件的 HTML / 源码）再原样贴回回答里——用户通过按钮获取文件。\n"
        "重要：你打开的每一个代码块都必须用一行 ``` 闭合。未闭合的代码围栏会让后续回答"
        "变成不可读的代码，并掩盖你写好的总结。"
    ),

    # Skill-switch quota exhausted message. Placeholders: {name}, {switch_count}, {quota}
    "skill_switch_limit": (
        "use_skill：已达技能切换上限（{switch_count}/{quota}），"
        "无法加载「{name}」。请回复「继续」以追加额度后自动重试。"
    ),

    # Self-heal prompt for malformed tool calls. Placeholders: {tool_name}, {snippet}
    "selfheal": (
        "你上一次的工具调用不是合法 JSON，无法被解析。"
        "你现在必须调用工具 `{tool_name}`。\n\n"
        "## 你上一次的（非法）输出\n{snippet}\n\n"
        "## 要求\n"
        '- 只输出一个纯 JSON 对象：{"tool": "{tool_name}", "arguments": {...}}\n'
        "- 只能使用双引号（\"）；绝对不要使用单引号、`=>`、`--code` 或 [TOOL_CALL] 标签\n"
        "- 字符串值内部的双引号用 \\\" 转义，换行用 \\n\n"
        "- JSON 前后不要附加任何解释文字"
    ),

    # Final-stage constraint note appended to the user turn when no tools ran
    # but the skill prompt asked for tool use. Resolved per prompt_language.
    "final_stage_note": (
        "\n\n## ⚠️ 当前阶段：最终回答生成\n\n"
        "这是最终生成阶段，已无法调用工具。请直接用自然语言回答用户的问题。"
        "绝对不要输出 [TOOL_CALL]、JSON 格式的工具调用，或任何伪装成工具调用的代码块。"
        "如果用户的问题需要工具但没有任何工具被执行，请如实告知用户。"
    ),
}
