"""Session-scoped administrator reconciliation for uncertain image sends."""

import asyncio

from ..utils.access_policy import can_manage_chat
from ..utils.delivery_guard import get_delivery_ledger


async def handle_delivery_command(event, action, argument=""):
    if not await can_manage_chat(event):
        yield event.plain_result("仅机器人管理员、群主或群管理员可以核实本群 Pixiv 发送记录。")
        return
    session = str(getattr(event, "unified_msg_origin", "") or "")
    if not session:
        yield event.plain_result("无法确定当前会话，未更改发送记录。")
        return
    try:
        ledger = get_delivery_ledger()
        if action == "delivery_status":
            rows = await asyncio.to_thread(ledger.unresolved, session)
            if not rows:
                yield event.plain_result("当前会话没有待核实的 Pixiv 发送记录。")
                return
            lines = [f"当前会话有 {len(rows)} 项发送结果待核实："]
            for row in rows[:20]:
                label = "结果未知" if row["state"] == "unknown" else "发送中或执行中断"
                lines.append(f"{row['illust_id']}：{label}，{row['updated_at']}")
            if len(rows) > 20:
                lines.append("仅显示最早的 20 项，处理后可再次查看。")
            lines.append("核对群内消息后使用 /pixiv 确认送达 <作品ID>；确认未收到才使用 /pixiv 允许重发 <作品ID>。")
            lines.append("允许重发只解除限制，不立即发送；误判可能导致重复。发送中记录需等待或由维护人员核查。")
            yield event.plain_result("\n".join(lines))
            return
        if action not in {"delivery_confirm", "delivery_retry"} or not argument.isascii() or not argument.isdigit() or not 0 < int(argument) < 2**63:
            yield event.plain_result("请输入有效的作品ID。")
            return
        illust_id = int(argument)
        rows = await asyncio.to_thread(ledger.status, session, [illust_id])
        row = rows.get(illust_id)
        if not row or row["state"] != "unknown":
            yield event.plain_result("此作品没有可处理的结果未知记录。发送中或执行中断的记录需由维护人员核查。")
            return
        resolution = "sent" if action == "delivery_confirm" else "failed"
        await asyncio.to_thread(
            ledger.reconcile, session, [illust_id], row["attempt_token"], resolution,
            "Chat administrator confirmed delivery" if resolution == "sent" else "Chat administrator explicitly authorized retry",
        )
        text = "已记录送达，下轮订阅检查将恢复处理。" if resolution == "sent" else "已解除此作品的重发限制；下轮推送可再次尝试，本次未发送图片。"
        yield event.plain_result(text)
    except Exception:
        yield event.plain_result("发送记录读取或更新失败，未解除防重限制。请稍后核查。")
