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
        "## Examples (output the number only)\n"
        "- User: 'tell me a joke' (no matching skill in the list) -> Number: 0\n"
        "- User: 'refactor this Python code' (if the list has a coding/refactor skill, "
        "return its number; otherwise 0)\n\n"
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
        "{tool_desc}\n\n"
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
        "- NEVER fabricate File, file paths, or UUIDs.\n"
        "\n"
        "## Path discipline\n"
        "- **About skill-provided paths**: if a skill's instructions hardcode an absolute path (e.g. `/app/workspace/xxx.html`, `/app/xxx.html`), ignore it and write using a **relative path** inside the current working directory instead (e.g. `open(\"guangzhou_weather.html\", \"w\", encoding=\"utf-8\")`). All generated files must land within run_python's cwd; otherwise the process has no write permission for that path and the write fails with `Operation not permitted`.\n"
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

    # Appended to the Scheduled Task Rule at creation time. Forbids the model from
    # writing scripts that silently substitute placeholder data when a real external
    # fetch fails. No placeholders.
    "cron_no_fallback_rule": (
        "### Data Integrity (MANDATORY for any script you write)\n"
        "A scheduled task runs unattended, so a script that silently invents data will "
        "keep reporting success forever while producing garbage. Therefore:\n"
        "- If the task depends on external data (an HTTP API, a web page, a feed), the "
        "script MUST actually perform that request at runtime.\n"
        "- If the request fails for ANY reason (network blocked, DNS failure, timeout, "
        "non-2xx status, unparseable body), the script MUST write the error to stderr and "
        "exit with a NON-ZERO status code (e.g. `sys.exit(1)`). A failed run must be "
        "visible as a FAILED run.\n"
        "- NEVER substitute hard-coded, cached, simulated, estimated or 'example' values "
        "for data you could not fetch. No silent fallback, no `try/except` that swallows "
        "the error and continues with placeholder numbers.\n"
        "- NEVER label output with a data source you did not actually reach. Writing "
        "'Source: <API name>' in a report built from invented values is strictly "
        "forbidden.\n"
        "- Do not write a partial-success path that still exits 0 after producing an "
        "incomplete artifact."
    ),

    # Tells the model the live sandbox egress policy BEFORE it writes task code, so it
    # can refuse impossible tasks up front instead of improvising at runtime.
    # Used on the CREATION path (the model may still decline to create the job).
    # Placeholders: {mode_line}
    "sandbox_network_rule": (
        "### Sandbox Network Policy (current, authoritative)\n"
        "{mode_line}\n"
        "Take this into account BEFORE writing any code. If the task requires reaching a "
        "host that this policy forbids, the task is IMPOSSIBLE — do NOT emit the cron "
        "JSON, and do NOT write a script that pretends to work. Instead reply in plain "
        "text explaining that the sandbox network policy blocks the required access, name "
        "the host(s) needed, and tell the user to allow them in Settings → Sandbox "
        "Network (or switch the task to something that needs no external data)."
    ),

    # Same policy statement, but for the EXECUTION path, where the job already exists
    # and declining to "create" it is not an option — the run must fail loudly instead.
    # Placeholders: {mode_line}
    "sandbox_network_rule_exec": (
        "### Sandbox Network Policy (current, authoritative)\n"
        "{mode_line}\n"
        "Take this into account BEFORE writing any code. If this task requires reaching a "
        "host that the policy forbids, do NOT try to work around it and do NOT substitute "
        "placeholder data. Report the run as FAILED and state plainly that the sandbox "
        "network policy blocked the required access, naming the host(s) needed."
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

    # Always-on final-answer suffix (merged from the old branch-only final_stage_note):
    # forbids tool-call output in the final stage (D1 fix) and asks for a one-line
    # divergent closing. Appended to the system prompt in agent_graph.build_generation_messages.
    "final_answer_guidance": (
        "## Final Answer Stage Guidance\n"
        "You are now in the FINAL ANSWER generation stage and have NO tool-calling "
        "capability. Reply to the user directly in natural language. "
        "NEVER output [TOOL_CALL], a JSON-formatted tool call, or any code block "
        "disguised as a tool call. "
        "If the user's task requires a tool but none was executed this turn, tell the "
        "user honestly.\n\n"
        "## Answer length & format\n"
        "Lead with a DIRECT conclusion in NO MORE THAN 3 sentences, then expand only as "
        "needed. When citing sources, inline them (e.g. '[Source: Doc Name]') rather than "
        "dumping a list at the end.\n\n"
        "## Closing Suggestion\n"
        "At the very end of your answer, add ONE separate line with a 'divergent' touch — "
        "either:\n"
        "- a follow-up question that DEEPENS the current topic; OR\n"
        "- a next-step suggestion from a RELATED but DIFFERENT angle.\n"
        "Rules:\n"
        "- ONE sentence only, under 25 words. Do NOT repeat what you already said.\n"
        "- It must OPEN a new direction, not summarize.\n"
        "- If the user only asked you to generate a file / do one concrete one-off task "
        "with no natural extension, you MAY omit this line (and in that case keep the "
        "answer minimal per the File-generation Answer Rule — do not append anything).\n"
        "- Write it in the same language as the user."
    ),
    # Cron confirmation messages shown to the user (follows prompt_language).
    "cron_created_confirm": "Scheduled task '{name}' created. View it on the Scheduled Tasks page.",
    "cron_created_confirm_detail": "Scheduled task '{name}' created. Next run: {next_run} ({tz}). View it on the Scheduled Tasks page.",
    "cron_created_fallback": "Scheduled task created.",

    # ── Conversation-history compression prompts (Layer 1). No placeholders. ──
    "summary_prompt": (
        "You are a conversation compressor. Compress the following dialogue into a "
        "coherent English summary, preserving: key facts, user preferences, decisions "
        "made, open questions, and important conclusions or follow-ups. Do not quote "
        "verbatim; do not drop critical context. Output plain-text summary only, no "
        "markdown code fences."
    ),
    "summary_recompact_prompt": (
        "You are a summary compressor. The text below is an existing conversation "
        "summary. Compress it further into a shorter summary, preserving all key "
        "facts, user preferences, decisions, open questions, and important conclusions; "
        "drop redundant wording. Plain text, no markdown code fences."
    ),
    "query_condensed_warning": (
        "Your message was too long and has been condensed to fit the context window "
        "(head and tail kept verbatim, middle summarized)."
    ),
    "history_compressing": (
        "Conversation history is long; compressing earlier content to free up context "
        "space, please wait..."
    ),
    "assembly_trim_warning": (
        "Some earlier context (summary / conversation history / reference documents / "
        "tool records) was automatically trimmed to fit the context window so this "
        "response could be generated."
    ),
    "tool_desc_heading": "## Available tools",

    # update_memory 'write' rejected because the supplied text exceeds the 2000-char
    # cap. The write is NOT performed (no silent truncation). The model must surface
    # this to the user and point them to the Profile page. Follows prompt_language.
    "memory_too_long": (
        "User memory is too long to save. This write was "
        "rejected and no changes were made. Inform the user that the memory exceeds "
        "the length limit, and advise them to edit it themselves on the Profile page."
    ),

    # Injected into an active skill's system prompt so the LLM knows where the skill root lives inside
    # the sandbox (REPL_SKILLS_DIR/<name>) and that it is read-only. This note does NOT enumerate placeholder
    # spellings; it teaches one principle: this skill's root is always $REPL_SKILLS_DIR/<name>, with scripts/
    # and other files under it, so any example path referring to this skill maps to that root.
    # (The author-side {baseDir} is expanded by agent_nodes at skill-body load time into $REPL_SKILLS_DIR/<folder>,
    # so the LLM sees the already-expanded form.)
    "skill_sandbox_note": (
        "## Skill resources in the sandbox\n"
        "This skill's root directory is always `$REPL_SKILLS_DIR/<skill_name>` (`<skill_name>` is this skill's "
        "folder name; `REPL_SKILLS_DIR` is a sandbox env var). Its `scripts/` subdir and any other file live "
        "UNDER that root — e.g. a script's full path is `$REPL_SKILLS_DIR/<skill_name>/scripts/<script>`.\n"
        "Whatever form the examples use to refer to this skill's path, map it to the real sandbox path:\n"
        "- If the example already shows `$REPL_SKILLS_DIR` (e.g. the expanded form of a `{baseDir}` placeholder): "
        "just copy the commands verbatim — no path construction needed.\n"
        "- If the example uses a placeholder naming this skill's root (e.g. `<skill_dir>`): replace that placeholder "
        "wholesale with `$REPL_SKILLS_DIR/<skill_name>`.\n"
        "- If the example uses a bare relative path (e.g. `scripts/foo.py`, no root prefix): prepend "
        "`$REPL_SKILLS_DIR/<skill_name>/`, giving `$REPL_SKILLS_DIR/<skill_name>/scripts/foo.py`.\n"
        "- If the example references no path at all (e.g. this skill has no scripts and only calls tools): you don't "
        "need to care about this path.\n"
        "- Never replace `$REPL_SKILLS_DIR` with a literal path (e.g. `/app/.ragclaw/skills/...` does not exist in "
        "the sandbox and will fail with 'file not found'). Always reference this skill via the `$REPL_SKILLS_DIR` "
        "variable; if you genuinely need an absolute path, get it with `echo $REPL_SKILLS_DIR` or "
        "`os.environ['REPL_SKILLS_DIR']`.\n"
        "You may read any file there directly with run_python's open(), or call the read_skill_resource tool. "
        "The skill folder is READ-ONLY: if you write into it, the write is redirected to a sandbox-local "
        "shadow copy and is NOT saved back to the shared store, so never rely on edits to skill files persisting."
    ),

    # Concise variant appended to the read_skill_resource tool description. No placeholders.
    "skill_resource_tool_note": (
        "The skill folder is mounted read-only under the sandbox env var `REPL_SKILLS_DIR`. "
        "`REPL_SKILLS_DIR` is the ROOT directory that CONTAINS each skill's folder (so this skill's full path is "
        "`$REPL_SKILLS_DIR/<skill_name>`) — it does NOT include the skill name. "
        "When invoking scripts, ALWAYS use the form `$REPL_SKILLS_DIR/<skill_name>/scripts/<script>`; "
        "never substitute `<sandbox_root>`/`<skill_name>` with a literal (e.g. `/app/.ragclaw/skills/...` does not "
        "exist and will fail with 'file not found'). If you truly need an absolute path, obtain it with "
        "`echo $REPL_SKILLS_DIR` or `os.environ['REPL_SKILLS_DIR']`; you can also open files from there with "
        "run_python. Writes there are redirected to a sandbox-local shadow copy and are NOT saved back to the "
        "shared store."
    ),

    # Injected into the system prompt to constrain the LLM to use the injected current date
    # instead of hardcoding a year from its training data.
    "current_date_note": (
        "## Current date\n"
        "The current date is **{date}** (timezone {tz}). For any relative-time phrasing ('today', 'this week', "
        "'this month', 'this year'), always anchor to this date — do not use a year from your training data, and "
        "do not guess or fabricate a year."
"\n"
"Note the distinction between the **current date** above and a search result's **publish date**: each result carries its own "
"publication time, which is the date that article or page was actually published - they are not the same. When you cite a "
"result, label its publication date accurately; never write a result's publish date as 'today', and never treat the current date as a result's publish date."
    ),
}
