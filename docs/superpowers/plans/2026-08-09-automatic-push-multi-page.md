# Automatic Push Exclusions And Multi-Page Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply configurable `NTR` and `悪堕ち` exclusions to every automatic Pixiv push and send supported multi-page works atomically while enforcing random-push limits by actual image count.

**Architecture:** Keep manual list searches and LLM tools on their existing first-page behavior. Add reusable exclusion normalization in `utils/tag.py`, an automatic-push config field in `utils/config.py`, atomic page selection and message construction in `utils/pixiv_utils.py`, and explicit image-count accounting in `utils/random_search.py`; wire artist subscriptions and specific-ID lookup into the shared multi-page sender.

**Tech Stack:** Python 3, asyncio, aiohttp, AstrBot `MessageChain` and image components, peewee SQLite, unittest, remote `astrbot-dev-test` sidecar, AstrBot Dashboard target-plugin reload API.

## Global Constraints

1. `1` through `9` Pixiv pages are sent together in one message; `10` or more pages send only the first `3`.
2. Works with `10` or more pages include an explicit `作品ID: <illust_id>` detail line.
3. Random `return_count` is an image target. A work may exceed the remaining count, then selection stops before the next work ID.
4. A multi-page work is prepared completely before one send call. Any selected-page preparation failure fails the whole work attempt.
5. Only successfully sent work IDs enter `SentIllust`; skipped, failed, and unselected candidates remain uncached.
6. Automatic exclusions apply to random tags, random rankings, artist subscriptions, and future automatic subscriptions. Manual searches, LLM tools, and novels retain current behavior.
7. Random pushes remain ordinary chat messages. Existing QQ accepted-send timeout handling remains unchanged.
8. Production work is restricted to `astrbot_plugin_pixiv_reborn`; do not restart, stop, recreate, or rebuild the production `astrbot` container.
9. Dependency-complete tests run in `astrbot-dev-test` before production synchronization.

---

## File Map

`utils/tag.py` owns automatic exclusion normalization and merge semantics.

`utils/config.py`, `_conf_schema.json`, `README.md`, and `data/helpmsg.json` expose `automatic_push_excluded_tags` consistently.

`utils/pixiv_utils.py` owns page selection, atomic image component preparation, one-message construction, and partial temporary-file cleanup.

`utils/random_search.py` owns automatic exclusion merging, random delivery result metadata, image-budget accounting, send attempts, and post-send cache writes.

`utils/subscription.py` applies automatic exclusions and opts artist updates into multi-page delivery.

`handlers/illust.py` opts specific-ID lookup into the capped one-message multi-page sender.

`tests/test_tag_filters.py`, `tests/test_config_defaults.py`, `tests/test_metadata_and_schema.py`, `tests/test_multi_page_delivery.py`, `tests/test_random_push_retry.py`, and `tests/test_subscription_delivery.py` cover the behavioral contract.

---

### Task 1: Automatic Push Exclusion Configuration

**Files:**

- Modify: `utils/tag.py`
- Modify: `utils/config.py`
- Modify: `_conf_schema.json`
- Modify: `README.md`
- Modify: `data/helpmsg.json`
- Modify: `tests/test_tag_filters.py`
- Modify: `tests/test_config_defaults.py`
- Modify: `tests/test_metadata_and_schema.py`

**Interfaces:**

- Produces: `normalize_excluded_tags(value) -> list[str]`
- Produces: `merge_excluded_tags(*values) -> list[str]`
- Produces: `PixivConfig.automatic_push_excluded_tags: list[str]`
- Produces: Dashboard and command configuration key `automatic_push_excluded_tags`, stored as a comma-separated string.

- [ ] **Step 1: Add failing normalization and default tests**

Add tests equivalent to:

```python
from utils.tag import merge_excluded_tags, normalize_excluded_tags


def test_normalize_automatic_exclusions_accepts_common_separators():
    assert normalize_excluded_tags(" NTR，悪堕ち、ntr ") == ["ntr", "悪堕ち"]


def test_merge_automatic_exclusions_preserves_first_seen_order():
    assert merge_excluded_tags(["custom", "NTR"], "ntr,悪堕ち") == [
        "custom",
        "ntr",
        "悪堕ち",
    ]
```

Extend `PixivConfigDefaultsTest`:

```python
def test_automatic_push_exclusions_default_to_required_tags(self):
    config = PixivConfig({})
    self.assertEqual(config.automatic_push_excluded_tags, ["ntr", "悪堕ち"])

def test_automatic_push_exclusions_can_be_disabled(self):
    config = PixivConfig({"automatic_push_excluded_tags": ""})
    self.assertEqual(config.automatic_push_excluded_tags, [])
```

Extend metadata/schema coverage to require a string field with default `NTR,悪堕ち` and matching command-manager exposure.

- [ ] **Step 2: Run focused tests and confirm the missing-interface failures**

Run in the sidecar test checkout:

```bash
python -m unittest tests.test_tag_filters tests.test_config_defaults tests.test_metadata_and_schema -v
```

Expected: FAIL because the normalization functions and configuration field do not exist.

- [ ] **Step 3: Implement normalization and configuration loading**

Add to `utils/tag.py`:

```python
def normalize_excluded_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = value.replace("，", ",").replace("、", ",")
        values = normalized.split(",")
    else:
        values = list(value)

    result = []
    seen = set()
    for value_item in values:
        item = str(value_item or "").strip()
        if item.startswith(("-", "－", "—", "–")):
            item = item[1:].strip()
        key = item.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def merge_excluded_tags(*values) -> list[str]:
    merged = []
    for value in values:
        merged.extend(normalize_excluded_tags(value))
    return normalize_excluded_tags(merged)
```

Import `normalize_excluded_tags` in `utils/config.py` and load:

```python
self.automatic_push_excluded_tags = normalize_excluded_tags(
    self.config.get("automatic_push_excluded_tags", "NTR,悪堕ち")
)
```

Add the field to `PixivConfigManager.schema`, `display_keys`, `get_config_info()`, `_conf_schema.json`, README configuration table, and `data/helpmsg.json`. The Dashboard schema entry is:

```json
"automatic_push_excluded_tags": {
  "description": "自动推送统一排除标签",
  "type": "string",
  "hint": "随机标签、随机排行榜和画师订阅共同应用。使用逗号分隔；空字符串表示关闭。",
  "default": "NTR,悪堕ち"
}
```

- [ ] **Step 4: Run focused tests and confirm they pass**

```bash
python -m unittest tests.test_tag_filters tests.test_config_defaults tests.test_metadata_and_schema -v
```

Expected: PASS with zero failures.

- [ ] **Step 5: Commit the configuration task**

```bash
git add utils/tag.py utils/config.py _conf_schema.json README.md data/helpmsg.json tests/test_tag_filters.py tests/test_config_defaults.py tests/test_metadata_and_schema.py
git commit -m "Add automatic push exclusions"
```

---

### Task 2: Apply Exclusions To Every Automatic Push Source

**Files:**

- Modify: `utils/random_search.py`
- Modify: `utils/subscription.py`
- Modify: `tests/test_random_push_retry.py`
- Create: `tests/test_subscription_delivery.py`

**Interfaces:**

- Consumes: `merge_excluded_tags(*values) -> list[str]` from Task 1.
- Consumes: `PixivConfig.automatic_push_excluded_tags` from Task 1.
- Produces: `RandomSearchService._build_filter_config()` with merged per-source and automatic exclusions.
- Produces: `SubscriptionService.check_artist_updates()` filtering with automatic exclusions.

- [ ] **Step 1: Add failing automatic-source tests**

Extend random tests with:

```python
def test_random_filter_config_merges_source_and_automatic_exclusions(self):
    service.pixiv_config.automatic_push_excluded_tags = ["ntr", "悪堕ち"]
    config = service._build_filter_config("random:test", ["custom", "ntr"], "905956314")
    self.assertEqual(config.excluded_tags, ["custom", "ntr", "悪堕ち"])
```

Add subscription coverage that patches `filter_items`, invokes `check_artist_updates()`, and asserts:

```python
self.assertEqual(captured_excluded_tags, ["ntr", "悪堕ち"])
```

The random-ranking test must call `_build_filter_config(..., exclude_tags=[], ...)` and receive the same automatic exclusions.

- [ ] **Step 2: Run the focused tests and confirm exclusions are absent**

```bash
python -m unittest tests.test_random_push_retry tests.test_subscription_delivery -v
```

Expected: FAIL because random and artist automatic sources do not merge the new configuration.

- [ ] **Step 3: Merge exclusions at the automatic-source boundaries**

In `utils/random_search.py`:

```python
excluded_tags = merge_excluded_tags(
    exclude_tags,
    self.pixiv_config.automatic_push_excluded_tags,
)
```

Pass this merged value into `FilterConfig` for both random tags and rankings through `_build_filter_config()`.

In `utils/subscription.py`:

```python
filtered_illusts, _ = filter_items(
    [illust],
    f"画师订阅: {sub.target_name}",
    excluded_tags=self.pixiv_config.automatic_push_excluded_tags,
)
```

Keep the existing filtered-work cursor advancement behavior.

- [ ] **Step 4: Run the focused tests and confirm they pass**

```bash
python -m unittest tests.test_random_push_retry tests.test_subscription_delivery -v
```

Expected: PASS with random tags, rankings, and artist subscriptions covered.

- [ ] **Step 5: Commit the automatic-source wiring**

```bash
git add utils/random_search.py utils/subscription.py tests/test_random_push_retry.py tests/test_subscription_delivery.py
git commit -m "Apply exclusions to automatic Pixiv pushes"
```

---

### Task 3: Atomic Multi-Page Message Construction

**Files:**

- Modify: `utils/pixiv_utils.py`
- Create: `tests/test_multi_page_delivery.py`

**Interfaces:**

- Produces: `get_illust_delivery_image_count(illust, send_all_pages: bool) -> int`
- Preserves: `send_pixiv_image(..., send_all_pages: bool = False)` call compatibility.
- Changes: `send_all_pages=True` yields exactly one `MessageChain` for a normal illustration.
- Preserves: ugoira behavior and the existing image-quality fallback order.

- [ ] **Step 1: Add failing page-policy and one-message tests**

Create fake Pixiv objects with `page_count`, `meta_pages`, `meta_single_page`, `image_urls`, and `id`. Cover:

```python
def test_delivery_image_count_boundaries():
    self.assertEqual(get_illust_delivery_image_count(fake_illust(1), True), 1)
    self.assertEqual(get_illust_delivery_image_count(fake_illust(2), True), 2)
    self.assertEqual(get_illust_delivery_image_count(fake_illust(9), True), 9)
    self.assertEqual(get_illust_delivery_image_count(fake_illust(10), True), 3)
    self.assertEqual(get_illust_delivery_image_count(fake_illust(20), True), 3)
    self.assertEqual(get_illust_delivery_image_count(fake_illust(9), False), 1)
```

Patch image building so a nine-page work produces nine fake `Image` components. Consume the generator and assert one yielded `MessageChain`, nine image components, and one details component.

For a ten-page work, assert three image components and exactly one `作品ID: 148023016` occurrence.

For file-mode partial failure, make page 1 build a temporary component and page 2 fail; assert the generator produces only the failure marker and the page-1 file is removed.

- [ ] **Step 2: Run the focused test and confirm current per-page behavior fails**

```bash
python -m unittest tests.test_multi_page_delivery -v
```

Expected: FAIL because current `send_pixiv_image()` yields one message per page and has no ten-page cap.

- [ ] **Step 3: Implement page selection and atomic message construction**

Add:

```python
MULTI_PAGE_FULL_LIMIT = 9
MULTI_PAGE_OVERFLOW_COUNT = 3


def get_illust_delivery_image_count(illust, send_all_pages: bool) -> int:
    if getattr(illust, "type", None) == "ugoira":
        return 1
    if not send_all_pages:
        return 1
    page_count = max(1, int(getattr(illust, "page_count", 1) or 1))
    return page_count if page_count <= MULTI_PAGE_FULL_LIMIT else MULTI_PAGE_OVERFLOW_COUNT
```

Refactor `send_pixiv_image()` so it determines the selected URL sources, prepares all image components into one list, and yields once:

```python
selected_count = get_illust_delivery_image_count(illust, send_all_pages)
selected_sources = _select_illust_url_sources(illust, selected_count)
components = []
async with aiohttp.ClientSession() as session:
    for url_obj in selected_sources:
        component = await _prepare_image_component(session, url_obj)
        if component is None:
            await cleanup_pixiv_temp_files(components)
            yield event.plain_result("图片下载失败，仅发送信息：\n" + (detail_message or ""))
            return
        components.append(component)

details = detail_message or ""
if int(getattr(illust, "page_count", 1) or 1) >= 10:
    details = f"{details}\n作品ID: {illust.id}".strip()
if show_details and details:
    components.append(Plain(details))
yield event.chain_result(components)
```

Extract `_prepare_image_component()` from the existing quality loop without changing `original`, `large`, `medium`, proxy, compression, or send-method behavior. Ensure `cleanup_pixiv_temp_files()` accepts the partial component list already supported by `_extract_pixiv_temp_paths()`.

- [ ] **Step 4: Run multi-page and existing temporary-file tests**

```bash
python -m unittest tests.test_multi_page_delivery tests.test_pixiv_temp_cleanup -v
```

Expected: PASS with one message per work and no temporary-file leak.

- [ ] **Step 5: Commit the atomic sender**

```bash
git add utils/pixiv_utils.py tests/test_multi_page_delivery.py tests/test_pixiv_temp_cleanup.py
git commit -m "Send multi-page Pixiv works atomically"
```

---

### Task 4: Random Push Image Budget And Cache Semantics

**Files:**

- Modify: `utils/random_search.py`
- Modify: `tests/test_random_push_retry.py`

**Interfaces:**

- Consumes: `get_illust_delivery_image_count()` from Task 3.
- Produces: immutable `RandomIllustDeliveryResult` with `illust_ids: frozenset[int]` and `image_count: int`.
- Changes: `_send_random_illust_with_retry(...) -> RandomIllustDeliveryResult`.
- Extends: `RandomSearchExecutionResult` with `sent_image_count: int = 0`, preserving `sent_count` as successfully sent work-ID count.

- [ ] **Step 1: Add failing budget and cache tests**

Add a result type expectation:

```python
result = await service._send_random_illust_with_retry(...)
self.assertEqual(result.illust_ids, frozenset({148023016}))
self.assertEqual(result.image_count, 2)
```

Add a budget test with `return_count=3` and candidates containing `page_count` values `[1, 5, 1]`. Patch successful sending and assert:

```python
self.assertEqual(sent_candidate_ids, [1, 2])
self.assertEqual(result.sent_count, 2)
self.assertEqual(result.sent_image_count, 6)
self.assertEqual(sent_records, [(1, chat_id), (2, chat_id)])
self.assertNotIn((3, chat_id), sent_records)
```

Add a ten-page test showing image count `3`, and retain tests proving failed candidates never reach `add_sent_illust()`.

- [ ] **Step 2: Run random-push tests and confirm work-ID counting fails the new contract**

```bash
python -m unittest tests.test_random_push_retry -v
```

Expected: FAIL because current code stops on `len(sent_illust_ids)` and returns a plain set.

- [ ] **Step 3: Implement explicit random delivery metadata**

Add:

```python
@dataclass(frozen=True)
class RandomIllustDeliveryResult:
    illust_ids: frozenset[int] = frozenset()
    image_count: int = 0
```

Call the shared sender with capped multi-page mode:

```python
async for message_content in send_pixiv_image(
    self.client,
    mock_event,
    illust,
    detail_message,
    show_details=config.show_details,
    send_all_pages=True,
):
```

After `_send_message_with_attempt_record()` returns the current ID, return:

```python
return RandomIllustDeliveryResult(
    illust_ids=frozenset(sent_ids),
    image_count=get_illust_delivery_image_count(illust, True),
)
```

Return the empty result for every failed attempt. In `_send_random_illusts_with_fallback()` maintain `sent_image_count`, check it before each next candidate, write each successful ID immediately, and accumulate the result image count:

```python
if sent_image_count >= target_count:
    break
delivery = await self._send_random_illust_with_retry(...)
if delivery.illust_ids:
    for illust_id in delivery.illust_ids - sent_illust_ids:
        await asyncio.to_thread(add_sent_illust, illust_id, chat_id)
    sent_illust_ids.update(delivery.illust_ids)
    sent_image_count += delivery.image_count
```

Return both work and image counts from `RandomSearchExecutionResult`.

- [ ] **Step 4: Run random-push tests and confirm budget, retries, and cache behavior**

```bash
python -m unittest tests.test_random_push_retry tests.test_random_search_send_attempt -v
```

Expected: PASS, including current QQ accepted-timeout tests.

- [ ] **Step 5: Commit the random budget task**

```bash
git add utils/random_search.py tests/test_random_push_retry.py
git commit -m "Count random Pixiv pushes by delivered images"
```

---

### Task 5: Artist Subscription And Specific-ID Multi-Page Wiring

**Files:**

- Modify: `utils/subscription.py`
- Modify: `handlers/illust.py`
- Modify: `tests/test_subscription_delivery.py`
- Modify: `tests/test_multi_page_delivery.py`

**Interfaces:**

- Consumes: atomic `send_pixiv_image(..., send_all_pages=True)` from Task 3.
- Preserves: artist `last_notified_illust_id` advancement only after successful image sending.
- Changes: specific-ID lookup sends through the atomic normal-message sender and applies the same 9/3 page rule.

- [ ] **Step 1: Add failing caller-wiring tests**

Patch `send_pixiv_image` and capture keyword arguments for `SubscriptionService.send_update()`:

```python
self.assertTrue(captured_kwargs["send_all_pages"])
```

For the specific-ID handler, patch the shared sender, enable `forward_threshold`, invoke `pixiv_specific`, and assert the direct atomic sender receives `send_all_pages=True` while `send_forward_message` is not called.

Add a subscription failure test proving `check_artist_updates()` leaves `last_notified_illust_id` unchanged when any selected page fails.

- [ ] **Step 2: Run caller tests and confirm current defaults fail**

```bash
python -m unittest tests.test_subscription_delivery tests.test_multi_page_delivery -v
```

Expected: FAIL because artist subscriptions omit `send_all_pages=True`, and specific-ID lookup still branches into forwarding.

- [ ] **Step 3: Opt the required callers into atomic multi-page delivery**

In `SubscriptionService.send_update()`:

```python
async for message_content in send_pixiv_image(
    self.client,
    mock_event,
    illust,
    detail_message,
    self.pixiv_config.show_details,
    send_all_pages=True,
):
```

Keep explicit `subscription_force_forward` behavior by wrapping the one atomic chain in one node only when that setting is true.

In `handlers/illust.py`, remove the `forward_threshold` branch for the specific-ID command and always call `send_pixiv_image(..., send_all_pages=True)`. This preserves ordinary-message presentation for the explicit work lookup while list-search forwarding remains configurable.

- [ ] **Step 4: Run focused caller tests**

```bash
python -m unittest tests.test_subscription_delivery tests.test_multi_page_delivery -v
```

Expected: PASS with capped multi-page wiring and subscription cursor protection.

- [ ] **Step 5: Commit caller wiring**

```bash
git add utils/subscription.py handlers/illust.py tests/test_subscription_delivery.py tests/test_multi_page_delivery.py
git commit -m "Use multi-page delivery for Pixiv subscriptions"
```

---

### Task 6: Sidecar Verification, Production Configuration, And Target-Only Release

**Files:**

- Verify: all repository files changed in Tasks 1 through 5.
- Update on production: `/volume1/docker/astrbot/data/config/astrbot_plugin_pixiv_reborn_config.json`
- Synchronize on production: only changed files under `/volume1/docker/astrbot/data/plugins/astrbot_plugin_pixiv_reborn`
- Back up under: `/volume1/docker/astrbot/data/plugin_data/pixiv_search/codex_backups`

**Interfaces:**

- Consumes: verified commits from Tasks 1 through 5.
- Produces: production config with `"automatic_push_excluded_tags": "NTR,悪堕ち"`.
- Produces: one target-plugin Dashboard reload request with body `{"name":"astrbot_plugin_pixiv_reborn"}`.

- [ ] **Step 1: Refresh the sidecar checkout and run the full test suite**

Use the existing sidecar checkout at:

```text
/work/_codex_pixiv_test/pixiv-20260621-authdb/astrbot_plugin_pixiv_reborn
```

Synchronize the repository files into that development checkout, excluding `.git`, caches, bytecode, and local scratch files. Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 2: Run static and metadata checks**

Run in the sidecar:

```bash
python -m compileall -q -f .
python -c "import json, pathlib; json.loads(pathlib.Path('_conf_schema.json').read_text(encoding='utf-8')); print('schema ok')"
git diff --check
```

Expected: each command exits `0`; schema output is `schema ok`.

- [ ] **Step 3: Review the exact production file set and create backups**

Confirm `git status --short`, `git diff --stat d28841a..HEAD`, and changed file names. The fixed baseline `d28841a` is the approved design-specification commit. Back up every production file that will be replaced, the plugin config JSON, and `subscriptions.db` using timestamped names under:

```text
/volume1/docker/astrbot/data/plugin_data/pixiv_search/codex_backups
```

Use SQLite's backup API for `subscriptions.db`; do not stop the plugin or container.

- [ ] **Step 4: Synchronize only the target plugin files and write the visible config field**

Use `scp -O -P 44012` for individual changed files. Update the JSON object in place with Python so all existing keys and secrets remain untouched:

```python
config["automatic_push_excluded_tags"] = "NTR,悪堕ち"
```

Write UTF-8 JSON through a temporary sibling file followed by an atomic replace. Do not print the configuration object.

- [ ] **Step 5: Verify hashes and syntax before reload**

Compare SHA-256 for every synchronized file. Compile changed Python files in the production container without creating bytecode:

```bash
python -c "import pathlib,sys; [compile(pathlib.Path(p).read_text(encoding='utf-8'), p, 'exec') for p in sys.argv[1:]]" <changed-python-files>
```

Parse `_conf_schema.json` and verify the production config field by printing only a boolean and normalized tag list, with no credential-bearing content.

- [ ] **Step 6: Reload only the Pixiv plugin**

Generate a short-lived Dashboard JWT in memory from `cmd_config.json`, then call:

```http
POST http://127.0.0.1:16185/api/plugin/reload
Content-Type: application/json
Authorization: Bearer <in-memory-jwt>

{"name":"astrbot_plugin_pixiv_reborn"}
```

Expected: HTTP `200` and a successful target-plugin reload response. Never call reload without `name`.

- [ ] **Step 7: Verify production behavior without sending unsolicited test images**

Check:

1. `/api/plugin/get` reports `astrbot_plugin_pixiv_reborn` active.
2. Reload logs show exactly one termination and one load for the target plugin.
3. Logs show `pixiv_search_illust`, `pixiv_search_novel`, and `RandomSearchService` registration/start messages.
4. Config summary includes the automatic exclusion field without exposing secrets.
5. Recent target-plugin errors are absent.
6. Production `astrbot` remains `running` and `healthy`, with its start time unchanged.
7. A read-only SQLite query confirms the existing random tags, artist subscriptions, schedules, and `SentIllust` rows remain intact.

- [ ] **Step 8: Commit any verification-only documentation update and push the fork**

If README or changelog content changed during review, include it in the relevant earlier task commit. Confirm the worktree is clean, then:

```bash
git push origin main
```

Expected: `origin/main` points to the final verified commit. Do not create a PR against the upstream repository.

---

## Final Acceptance Checklist

- [ ] `automatic_push_excluded_tags` is visible in schema, command configuration, README, help text, and production config.
- [ ] Random tags, random rankings, and artist subscriptions merge `ntr` and `悪堕ち` into filtering.
- [ ] Manual list search, LLM tool, and novel behavior remain unchanged.
- [ ] Page counts `1`, `2`, `9`, `10`, and greater than `10` match the approved page policy.
- [ ] A normal multi-page work produces one ordinary message and one detail block.
- [ ] Random image budget can exceed the target only through the current complete work, then stops.
- [ ] Failed and unselected works never enter `SentIllust`.
- [ ] Partial file-mode preparation cleans temporary files.
- [ ] Sidecar full tests, compilation, schema parsing, and diff checks pass.
- [ ] Production synchronization is limited to the target plugin and one config key.
- [ ] Target-only reload succeeds and the production container start time is unchanged.
