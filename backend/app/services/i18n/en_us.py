"""English (en-US) agent-graph prompt templates — A/B variants.

Aim: improve instruction-following on English-dominant base models
(GPT/Claude/DeepSeek etc.). Selected when config_manager.prompt_language == 'en'.
Literal braces in JSON examples (e.g. `{}`, {"tool": ...}) are left as-is; the
renderer only substitutes {word} placeholders, so no double-brace escaping needed.
"""

MESSAGES = {
    # Layer-1 intent-router prompt. Placeholders: {query}, {skill_list}
    "intent_router": (
        "You are an intent router. Based on the user's question, select the most "
        "appropriate skill from the following NUMBERED list.\n\n"
        "Available skills:\n{skill_list}\n\n"
        "Rules:\n"
        "- If the user's question closely matches a skill, return that skill's NUMBER.\n"
        "- If the user's question does not match any skill, return 0.\n"
        "- Return ONLY a single integer (the skill number, or 0). No other output.\n\n"
        "User question: {query}\n\n"
        "Number:"
    ),

    # Forced tool-call JSON system prompt. Placeholder: {tool_desc}
    "tool_system": (
        "# ⚠️ CRITICAL INSTRUCTION: Decide whether to call a tool (or explicitly stop)\n\n"
        "Your job is to decide whether another tool call is still needed.\n\n"
        "## When to STOP calling tools (most important)\n"
        "- If the prior tool results ALREADY fully satisfy the user's request "
        "(e.g. the file was successfully created/modified, the computation is done), "
        "do NOT call any tool again.\n"
        "- Output ONLY an EMPTY object `{}` and the system will generate the final reply.\n"
        "- NEVER call the same tool again just to 'confirm' a result, and NEVER repeat a "
        "write/append that already achieved its goal.\n"
        "- Only keep calling tools when further action is genuinely required.\n\n"
        "## When you MUST use a tool\n"
        "- User asks to generate / create / write / save / append / modify a file -> MUST call run_python\n"
        "- User asks to run code, do data processing, or compute -> MUST call run_python\n"
        "- Any file read/write operation that is NOT yet done -> MUST call run_python\n"
        "- User asks to generate a diagram / HTML page / chart / visualization / web page / document / report -> MUST call run_python to write the file\n"
        "- Any request to PRODUCE or READ a workspace file / code -> MUST call run_python; NEVER answer such a request with plain text instead of a tool call\n\n"
        "## Available tools\n{tool_desc}\n\n"
        "## Output format\n"
        'When a tool is still needed: {"tool": "tool_name", "arguments": {"arg_name": "arg_value"}}\n'
        'When the task is done (no tool needed): {}\n\n'
        "## Rules\n"
        "- Output ONLY the JSON object above, with no extra text.\n"
        '- You MUST use double quotes (") and MUST NOT use single quotes (\').\n'
        "- You MUST NOT use => arrow syntax; use the standard JSON colon (:).\n"
        "- Do NOT wrap the JSON in ``` code fences.\n"
        "- Do NOT output [TOOL_CALL] or <tool_call> tags.\n"
        '- Escape double quotes inside code arguments as \\", and use \\n for newlines.\n'
        "- Do NOT output the final reply; if the task is done, output {} instead.\n"
        "- NEVER fabricate File, file paths, or UUIDs."
    ),

    # Forced run_python retry instruction (interception when intent needs a tool
    # but the model produced no tool call). No placeholders.
    "tool_force_retry": (
        "⚠️ This user request clearly requires a workspace file or code operation "
        "(e.g. generating an HTML page / diagram / chart / document, reading or modifying a file, "
        "or running code). You MUST now call run_python to fulfill it — never answer in plain text. "
        "Output a valid run_python tool call (as JSON or a code block) immediately."
    ),

    # Rule appended to the final-generation system prompt: when a tool produced
    # a downloadable file, the answer must not re-paste the source code. No placeholders.
    "file_answer_rule": (
        "## File-generation Answer Rule\n"
        "If your tool produced a downloadable file, the system sends the file metadata through a "
        "SEPARATE structured channel and displays it to the user as a SEPARATE download button — "
        "outside your answer text. Therefore your final answer should ONLY contain a brief "
        "sentence saying the file was generated; do NOT include any download link, a file path, "
        "or a URL in your answer. Also do NOT re-paste the source code / script that generated the "
        "file, and never paste the generated file's own content (e.g. the HTML/source of the file) "
        "back into your answer — the user gets the file via the button.\n"
        "IMPORTANT: always close every code block you open with a final ``` line. An unclosed code "
        "fence turns the rest of your answer into unreadable code and hides the summary you wrote."
    ),

    # Skill-switch quota exhausted message. Placeholders: {name}, {switch_count}, {quota}
    "skill_switch_limit": (
        'use_skill: skill-switch limit reached ({switch_count}/{quota}); '
        'cannot load "{name}". Reply "continue" to add quota and auto-retry.'
    ),

    # Self-heal prompt for malformed tool calls. Placeholders: {tool_name}, {snippet}
    "selfheal": (
        "Your previous tool call was NOT valid JSON and could not be parsed. "
        "You MUST call the tool `{tool_name}` now.\n\n"
        "## Your previous (invalid) output\n{snippet}\n\n"
        "## Requirements\n"
        '- Output ONLY a single pure JSON object: {"tool": "{tool_name}", "arguments": {...}}\n'
        "- Use double quotes (\") only. NEVER use single quotes, `=>`, `--code`, "
        "or [TOOL_CALL] tags.\n"
        '- Escape any double quotes inside string values as \\", and newlines as \\n.\n'
        "- Do NOT add any explanation before or after the JSON."
    ),

    # Final-stage constraint note appended to the user turn when no tools ran
    # but the skill prompt asked for tool use. Resolved per prompt_language.
    "final_stage_note": (
        "\n\n## ⚠️ Current stage: final answer generation\n\n"
        "This is the final generation stage; no tool-calling capability is available. "
        "Reply to the user's question directly in natural language. "
        "NEVER output [TOOL_CALL], a JSON-formatted tool call, or any code block disguised as a tool call. "
        "If the user's task requires a tool but none was executed, tell the user honestly."
    ),
}
