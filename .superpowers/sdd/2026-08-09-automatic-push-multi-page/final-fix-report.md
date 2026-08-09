# Pixiv 自动推送多页功能最终集中修正报告

## 状态

四项 Important 与两项 Minor 全部处理完成。生产修改限定在 `utils/pixiv_utils.py`、`utils/subscription.py`、`utils/random_search.py`，未进行重构。

## 根因

### 缺页元数据产生部分成功

`_get_illust_url_sources()` 会过滤缺少 `image_urls` 的页面，`get_illust_delivery_image_count()` 又使用可用来源数截断策略计数。三页作品只有两个有效 `meta_pages` 时，发送端据此构造两张图片；空 `meta_pages` 时仍可构造详情消息。随机发送端因此可能把不完整作品记为成功。

修正方式：策略计数只依据作品类型、`send_all_pages` 和 9 页及 3 页规则。发送前比较选定来源数与策略计数，数量不一致时返回现有准备失败标记，整个作品不产生图片消息。

### 订阅发送纯失败提示

`SubscriptionService.send_update()` 原有顺序是先调用 `context.send_message()`，随后检查原子消息中是否含有 `Image`。页面准备失败产生的纯文本消息已经进入群聊，函数才返回 `False`；游标虽然保留，同一提示会在后续检查中重复发送。

修正方式：发送前检查原始消息的顶层组件。缺少 `Image` 时静默丢弃并返回失败状态，画师订阅游标保留，下一次检查继续尝试同一作品。

### 订阅临时文件未清理

随机发送通过 `_send_message_with_attempt_record()` 的 `finally` 清理原始消息，订阅发送端缺少对应处理。强制转发场景又把图片包装进 `Nodes`，因此只能使用包装前的原子消息定位临时文件。

修正方式：订阅对每个非空原始消息使用 `try/finally`，普通发送、强制转发和发送异常结束后统一调用 `cleanup_pixiv_temp_files(message_content)`。

### Ugoira 缺少回归保护

`send_pixiv_image()` 的 `ugoira` 分支位于普通页面选择之前，计数函数返回一张，`send_ugoira()` 提供 GIF 成功消息与失败提示。生产行为存在，测试集没有保护委托顺序、计数、成功 GIF 和失败提示。

修正方式：增加四项回归测试，并通过临时变异证明计数和委托测试能够捕获分支破坏。成功与失败测试直接执行 `send_ugoira()`。

### 随机重试返回标注错误

`_send_random_illust_with_retry()` 实际返回 `RandomIllustDeliveryResult`，函数标注仍为 `set[int]`，与调用端访问 `illust_ids`、`image_count` 的行为不符。

修正方式：仅将返回标注改为 `RandomIllustDeliveryResult`，并使用 `typing.get_type_hints()` 验证。

### 失败图片计数缺少断言

随机发送实现只在成功结果中累加 `delivery.image_count`，现有混合失败和全失败测试只检查作品数与缓存，没有检查 `sent_image_count`。

修正方式：混合失败场景增加 `sent_image_count == 2`，全失败场景增加 `sent_image_count == 0`；临时将失败候选计入图片数后，两项测试均能捕获破坏。

## 红绿证据

基线：修改前相关范围 29 项通过。

RED：一次性加入六项发现对应的测试后运行 13 项，结果为 7 项失败。失败内容分别为缺页计数 `2 != 3`、空页计数 `0 != 3`、订阅连续发送两次失败提示、三种订阅发送场景遗留临时文件，以及返回标注仍为 `set[int]`。`ugoira` 与失败图片计数测试在当前正确实现上通过，符合两项“缺少测试”的审查性质。

GREEN：三个生产模块完成最小修改后，同一 13 项测试全部通过。

变异检查：临时破坏 `ugoira` 计数、委托分支和失败候选图片计数后运行 6 项，4 项按预期失败；恢复生产代码后最终相关范围全部通过。

最终相关测试命令：

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath '.tmp\test-deps').Path
python -m unittest tests.test_multi_page_delivery tests.test_subscription_delivery tests.test_random_push_retry tests.test_pixiv_temp_cleanup tests.test_random_search_send_attempt -v
```

结果：39 项通过，0 项失败，0 项错误。

语法检查：

```powershell
python -m py_compile utils/pixiv_utils.py utils/subscription.py utils/random_search.py tests/test_multi_page_delivery.py tests/test_subscription_delivery.py tests/test_random_push_retry.py
```

结果：6 个文件通过。

差异检查：

```powershell
git diff --check
```

结果：通过。

## 跨任务行为自查

`test_ten_page_default_delivery_does_not_append_work_id` 保护默认单页发送语义。

`test_specific_id_uses_atomic_sender_when_forwarding_is_enabled` 保护指定作品继续使用普通原子消息。

`test_automatic_excluded_candidates_are_never_sent_or_cached` 保护自动排除作品不发送且不写入 `SentIllust`。

`test_failed_image_is_retried_then_replaced_by_next_candidate` 与 `test_two_consecutive_failed_candidates_stop_silently` 保护失败作品不计图片数、不写缓存及静默替补行为。

`test_artist_updates_pass_automatic_exclusions_to_filtering` 保护排除作品的订阅游标行为；失败作品静默保留游标由连续两次检查测试覆盖。

`test_subscription_force_forward_wraps_atomic_chain_in_one_node` 保护强制转发包装；新增测试保护包装前原始链的文件清理。

`test_send_timeout_with_success_eventret_is_treated_as_accepted` 保护 QQ `sendMsg` 受理后回执超时仍按成功处理。

## 提交

父提交：`f027695`。

提交标题：`Fix Pixiv automatic multi-page final review findings`。

本报告属于该最终修正提交；提交号在提交完成后由最终响应记录，避免跟踪文件引用包含自身的提交号。

## 修改文件

生产代码：

- `utils/pixiv_utils.py`
- `utils/subscription.py`
- `utils/random_search.py`

测试代码：

- `tests/test_multi_page_delivery.py`
- `tests/test_subscription_delivery.py`
- `tests/test_random_push_retry.py`

报告：

- `.superpowers/sdd/2026-08-09-automatic-push-multi-page/final-fix-report.md`

## 关注项

Windows 沙箱会阻止 `TemporaryDirectory` 清理，最终测试在沙箱外执行。`test_random_search_send_attempt` 依赖 worktree 内现有 `.tmp/test-deps` 中的 `peewee`。

本次范围未包含真实 AstrBot 或 QQ 消息发送、生产配置修改、插件重载及生产日志检查。
