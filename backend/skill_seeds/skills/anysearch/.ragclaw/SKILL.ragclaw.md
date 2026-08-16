<!-- ragclaw-adapter:anysearch -->
## Resolved command (ragclaw pre-injected)

Resolved command (use directly): python3 $REPL_SKILLS_DIR/anysearch/.ragclaw/shim.py

## Output requirements

When you present search results to the user, you MUST follow these hard rules:
1. **Keep source links as markdown.** Every cited result MUST carry a clickable markdown link in the answer body: `[source name](full URL)` — e.g. `[China News](https://www.chinanews.com.cn/xxx)`. Never reduce a source to plain text (e.g. "Source: China News") and never drop the URL. The URL must be the real, complete, reachable link from the search result, not fabricated.
2. **List each result with its own link.** When a search returns multiple items, enumerate them and give every item its own source link.
3. **Distinguish the current date from the publish date.** The system injects the **current date** into the task background (reference only). When you say "today", mean that injected current date. A search result item's own publication time (publish date) is a SEPARATE fact — label it as the item's publish date, and NEVER write a result's publish date as "today", nor write the current date as a result's publish date.
