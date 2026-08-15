# Native FunctionTool Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy Pixiv LLM tool handler wrapper with AstrBot v4.27.3 native `FunctionTool` execution.

**Architecture:** Return the existing `FunctionTool` subclasses from the factory. Let AstrBot choose their overridden `call()` methods, and rely on the core manager's shallow `ToolSet` construction for shared object identity.

**Tech Stack:** Python 3.12, AstrBot v4.27.3 `FunctionTool`, `unittest`.

## Global Constraints

The registered names remain `pixiv_search_illust` and `pixiv_search_novel`.
`context.get_llm_tool_manager()`, `event.request_llm()`, and `ToolLoopAgentRunner` must use the same tool objects.
No AstrBot core, other plugin, container lifecycle, schema, or subscription data changes are permitted.
Production deployment uses target-plugin reload only.

---

### Task 1: Native FunctionTool execution

**Files:**
- Modify: `utils/llm_tool.py:698-764`
- Modify: `tests/test_llm_tool.py:195-232`

**Interfaces:**
- Consumes: `FunctionTool.call(context: ContextWrapper, **kwargs)` and `FunctionToolExecutor._execute_local(...)`.
- Produces: `create_pixiv_llm_tools(...) -> list[FunctionTool]` containing the original subclass instances with `handler is None`.

- [ ] **Step 1: Write the failing executor test**

Add a test that builds tools through `create_pixiv_llm_tools()`, verifies the Pixiv tool is the original subclass, and executes it with AstrBot's local `FunctionToolExecutor` using a fake run context. The test must fail before production changes because the wrapper sets `handler` and AstrBot selects `decorator_handler`.

- [ ] **Step 2: Run the focused test and record the expected failure**

Run: `python -m unittest tests.test_llm_tool -v`

Expected: failure showing the factory returned a wrapper with a non-null handler or the handler received an extra positional argument.

- [ ] **Step 3: Remove the legacy adapter**

Return `tool_impls` from `create_pixiv_llm_tools()`. Remove `_as_astrbot_function_tool`, its obsolete imports, and the manager monkey patch. Update module binding so native tools retain the plugin module and `handler_module_path` without assuming a handler exists.

- [ ] **Step 4: Verify focused and full tests**

Run: `python -m unittest tests.test_llm_tool -v`

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass, including object identity and native executor execution.

- [ ] **Step 5: Verify source integrity**

Run: `python -m py_compile utils/llm_tool.py tests/test_llm_tool.py`

Run: `git diff --check`

Expected: both commands exit with status 0.
