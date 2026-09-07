import type { TradeEvent } from "../types";

const LABELS: Record<string, string> = {
  "agent.dispatch": "分派专业 Agent", "tool.invoke": "调用业务工具", "tool.result": "工具返回结果", "plan.update": "更新任务计划",
  "context.compressed": "压缩对话上下文", "model.fallback": "切换备用模型", "final.result": "生成最终答复", error: "执行异常",
};

function summarize(event: TradeEvent): string {
  const p = event.payload ?? {};
  if (event.type === "agent.dispatch") return `${p.agent} · ${String(p.demands ?? "").slice(0, 52)}`;
  if (event.type === "tool.invoke") return `${p.tool} · ${JSON.stringify(p.args ?? {}).slice(0, 56)}`;
  if (event.type === "tool.result") {
    if (p.error) return `${p.tool} · ${p.error}`;
    if (p.hit_count !== undefined) return `${p.tool} · 命中 ${p.hit_count} 条${p.recall_strategy ? ` · ${p.recall_strategy}` : ""}`;
    if (p.order) return `${p.tool} · ${p.order.order_id} · ${p.order.status}`;
    return `${p.tool ?? "工具"} · 执行完成`;
  }
  if (event.type === "plan.update") return (p.tasks ?? []).map((task: any) => `${task.subject}[${task.state}]`).join(" · ");
  if (event.type === "context.compressed") return `摘要 ${p.summary_length} 字 · 保留 ${p.context_messages} 条上下文`;
  if (event.type === "model.fallback") return `${p.from} → ${p.to}`;
  if (event.type === "final.result") return "完整答复已生成并返回";
  return String(p.message ?? JSON.stringify(p).slice(0, 72));
}

export default function EventTimeline({ events }: { events: TradeEvent[] }) {
  const visible = events.filter((event) => event.type !== "token.delta").slice().reverse();
  return (
    <div className="timeline">
      {!visible.length && <div className="timeline-empty"><span>⌁</span><b>等待任务开始</b><p>发送购物需求后，这里会展示 Agent 的思考与工具调用过程。</p></div>}
      <ol>
        {visible.map((event, index) => (
          <li key={`${event.occurred_at}-${index}`} className={`ev ${event.type.replace(".", "-")}`}>
            <span className="event-node">{visible.length - index}</span>
            <div className="event-content"><div className="ev-head"><span>{LABELS[event.type] ?? event.type}</span><time>{event.occurred_at.slice(11, 19)}</time></div><p>{summarize(event)}</p></div>
          </li>
        ))}
      </ol>
    </div>
  );
}
