# Native FunctionTool Migration Design

## Goal

Make `pixiv_search_illust` and `pixiv_search_novel` execute through AstrBot
v4.27.3's native `FunctionTool.call(ContextWrapper, **kwargs)` branch.

## Design

`PixivIllustSearchTool` and `PixivNovelSearchTool` already subclass
`FunctionTool` and override `call()`. `create_pixiv_llm_tools()` will return
those instances without wrapping them in a second `FunctionTool` with a
legacy event handler. The registered tools therefore keep `handler=None`, so
AstrBot selects the native `call` branch and supplies the current
`ContextWrapper`.

The plugin will stop patching `FunctionToolManager.get_full_tool_set()`.
AstrBot v4.27.3 already constructs `ToolSet` from a shallow copy of
`func_list`, preserving each tool object. Existing identity assertions will
remain and cover the manager, request tool set, and LLM request.

## Compatibility

The public tool names, descriptions, schemas, search behavior, image sending,
and readable error returns remain unchanged. Plugin module binding remains on
the tool objects through `handler_module_path`; no handler module binding is
needed when `handler` is absent.

## Verification

Tests must demonstrate the current wrapper enters AstrBot's handler branch and
fails with an extra positional argument before the change. After the change,
the same tools must enter the native `call` branch, execute through
`FunctionToolExecutor`, and preserve object identity across all three tool
views.
