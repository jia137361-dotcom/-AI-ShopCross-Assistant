import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import EventTimeline from "./components/EventTimeline";
import ProductCards from "./components/ProductCards";
import type { ProductCard, TradeEvent } from "./types";

// 8000 被本机另一套 Docker 服务占用；ShopCross 本地开发默认使用 8001。
// 生产环境由 Nginx 将 API 与 WebSocket 代理到同源地址；本地开发保留后端默认端口。
const API_BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? window.location.origin : "http://127.0.0.1:8001");
const WS_BASE = API_BASE.replace(/^http/, "ws");
const SUGGESTIONS = [
  { icon: "◎", label: "查到手价", text: "我人在美国，250 美元预算买个降噪耳机寄到美国，到手价多少？" },
  { icon: "⌁", label: "智能选品", text: "推荐一款适合三日旅行的轻量防水背包，预算 500 元以内" },
  { icon: "◇", label: "选购指南", text: "跨境购买户外装备应该关注哪些参数和费用？" },
];

function loadOrCreate(key: string, prefix: string): string {
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
  localStorage.setItem(key, created);
  return created;
}

interface Turn { role: "buyer" | "agent"; text: string; }
interface SessionSummary { session_id: string; last_active_at: string; }
function AssistantMark() { return <span className="assistant-mark" aria-hidden="true">G</span>; }

// 工具检索发生在 final.result 前。将这些结果归属到同一轮 AI 回复，
// 让每次推荐成为聊天记录的一部分，而不是会话底部的全局商品区。
function recommendationsByReply(events: TradeEvent[]): ProductCard[][][] {
  const byReply: ProductCard[][][] = [];
  let pending: ProductCard[][] = [];
  for (const event of events) {
    if (event.type === "tool.result") {
      const hits = event.payload?.hits as ProductCard[] | undefined;
      if (hits?.length) pending.push(hits);
    }
    if (event.type === "final.result") {
      byReply.push(pending);
      pending = [];
    }
  }
  return byReply;
}

function newSessionId(): string {
  // crypto.randomUUID 在 HTTP/IP 访问时可能被浏览器禁用；部署尚未绑定 HTTPS
  // 时降级为 getRandomValues，保证首页快捷入口仍然可用。
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return `web-${crypto.randomUUID()}`;
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const values = crypto.getRandomValues(new Uint32Array(4));
    return `web-${Array.from(values, (value) => value.toString(36)).join("")}`;
  }
  return `web-${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}
function sessionLabel(sessionId: string): string { return `会话 ${sessionId.replace(/^web-/, "").toUpperCase()}`; }
interface AccountIdentity { token: string; username: string; }
function loadIdentity(): AccountIdentity | null {
  try {
    const saved = localStorage.getItem("shopcross.anonymousIdentity");
    if (!saved) return null;
    const identity = JSON.parse(saved) as AccountIdentity;
    return identity.token && identity.username ? identity : null;
  } catch { return null; }
}

export default function App() {
  const [sessionId, setSessionId] = useState(() => loadOrCreate("shopcross.session", "web"));
  const [identity, setIdentity] = useState<AccountIdentity | null>(loadIdentity);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [events, setEvents] = useState<TradeEvent[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [isHome, setIsHome] = useState(true);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const turnsRef = useRef<HTMLDivElement | null>(null);
  const conversationRef = useRef<HTMLElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const [orderNotice, setOrderNotice] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const authHeaders: Record<string, string> = identity ? { Authorization: `Bearer ${identity.token}` } : {};

  const authenticate = async () => {
    if (!username.trim() || !password) return;
    setAuthBusy(true); setAuthError("");
    try {
      const response = await fetch(`${API_BASE}/auth/${authMode}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const body = await response.json() as { token?: string; username?: string; detail?: string };
      if (!response.ok || !body.token || !body.username) throw new Error(body.detail || "操作失败，请稍后重试");
      const next = { token: body.token, username: body.username };
      localStorage.setItem("shopcross.anonymousIdentity", JSON.stringify(next));
      setIdentity(next); setPassword("");
    } catch (error) { setAuthError(error instanceof Error ? error.message : "操作失败，请稍后重试"); }
    finally { setAuthBusy(false); }
  };

  useEffect(() => {
    let cancelled = false;
    const restoreHistory = async () => {
      if (!identity) return;
      try {
        const response = await fetch(`${API_BASE}/commerce/sessions/${encodeURIComponent(sessionId)}/history`, { headers: authHeaders });
        if (response.status === 404) {
          // 浏览器可能保留了注册/登录前的本地会话 ID。该会话属于其他身份时，
          // 服务端按隔离规则返回 404；自动换新 ID，避免 WebSocket 无效重连。
          if (!cancelled) {
            const freshSessionId = newSessionId();
            localStorage.setItem("shopcross.session", freshSessionId);
            setTurns([]); setEvents([]); setStreaming(""); setSessionId(freshSessionId);
          }
          return;
        }
        if (!response.ok) return;
        const history = await response.json() as { turns: Turn[]; events: TradeEvent[] };
        if (!cancelled) {
          setTurns(history.turns ?? []);
          setEvents(history.events ?? []);
        }
      } catch {
        // 历史恢复失败不应阻断实时聊天；WebSocket 仍可接收本轮事件。
      }
    };
    void restoreHistory();
    return () => { cancelled = true; };
  }, [sessionId, identity]);

  const refreshSessions = async () => {
    if (!identity) return;
    try {
      const response = await fetch(`${API_BASE}/commerce/sessions`, { headers: authHeaders });
      if (!response.ok) return;
      const body = await response.json() as { sessions: SessionSummary[] };
      setSessions(body.sessions ?? []);
    } catch { /* 会话栏失败不影响聊天 */ }
  };

  useEffect(() => { void refreshSessions(); }, [identity, sessionId]);

  const switchSession = (nextSessionId: string) => {
    if (nextSessionId === sessionId) return;
    setMobileDrawerOpen(false);
    setIsHome(false);
    localStorage.setItem("shopcross.session", nextSessionId);
    setTurns([]); setEvents([]); setStreaming(""); setOrderNotice("");
    setSessionId(nextSessionId);
  };

  const createSession = () => {
    setMobileDrawerOpen(false);
    setIsHome(false);
    switchSession(newSessionId());
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  useEffect(() => {
    let closed = false;
    let retryTimer: number | undefined;
    let ws: WebSocket;
    const connect = () => {
      if (closed || !identity) return;
      ws = new WebSocket(`${WS_BASE}/commerce/events`);
      ws.onopen = () => {
        if (closed) return ws.close();
        ws.send(JSON.stringify({ shopping_session_id: sessionId, token: identity.token }));
      };
      ws.onclose = (event) => {
        setConnected(false);
        if (event.code === 4403) {
          // 本机残留的会话属于其他账号时，直接切换到当前账号的新会话，
          // 不继续拿同一个无权限 ID 重试。
          const freshSessionId = newSessionId();
          localStorage.setItem("shopcross.session", freshSessionId);
          setTurns([]); setEvents([]); setStreaming(""); setSessionId(freshSessionId);
          return;
        }
        if (!closed) retryTimer = window.setTimeout(connect, 1500);
      };
      ws.onmessage = (message) => {
        const rawEvent = JSON.parse(message.data) as { type?: string; payload?: Record<string, unknown> };
        if (rawEvent.type === "connection.ready") {
          setConnected(true);
          return;
        }
        const event = rawEvent as TradeEvent;
        if (event.type === "token.delta") {
          setStreaming((prev) => prev + (event.payload.token ?? ""));
          return;
        }
        setEvents((prev) => [...prev, event]);
        if (event.type === "final.result") {
          setStreaming("");
          setOrderNotice("");
          setTurns((prev) => [...prev, { role: "agent", text: event.payload.text ?? "" }]);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      ws?.close();
    };
  }, [sessionId, identity]);

  useEffect(() => {
    turnsRef.current?.scrollTo({ top: turnsRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, events, streaming, busy]);

  const submit = async (preset?: string, targetSessionId = sessionId, buyerTurnAlreadyShown = false) => {
    const query = (preset ?? input).trim();
    if (!query || busy || !identity) return;
    setInput("");
    setBusy(true);
    if (!buyerTurnAlreadyShown) setTurns((prev) => [...prev, { role: "buyer", text: query }]);
    try {
      const response = await fetch(`${API_BASE}/commerce/intents`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders },
        body: JSON.stringify({ shopping_session_id: targetSessionId, locale: "zh-CN", currency: "CNY", raw_query: query }),
      });
      if (!response.ok) throw new Error(`服务返回 ${response.status}`);
    } catch (error) {
      setTurns((prev) => [...prev, { role: "agent", text: `[error] 请求失败：${error}` }]);
    } finally { setBusy(false); void refreshSessions(); }
  };

  const hasConversation = turns.length > 0 || Boolean(streaming) || busy;
  const toolCount = events.filter((event) => event.type === "tool.result").length;
  const replyRecommendations = useMemo(() => recommendationsByReply(events), [events]);
  const beginOrder = (card: ProductCard, skuId: string) => {
    const sku = card.skus.find((item) => item.sku_id === skuId);
    setOrderNotice("已发起下单确认，请在会话中补全收货地址");
    void submit(`我要下单 ${card.title}（商品编号 ${card.product_id}，规格 ${sku?.spec ?? skuId}，SKU ${skuId}），数量 1。请先给我下单确认卡，并收集收货地址。`);
    // 商品卡在会话区下方；点击后主动把用户带回新产生的对话，而不是让回复停在屏幕外。
    window.requestAnimationFrame(() => {
      conversationRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      turnsRef.current?.scrollTo({ top: turnsRef.current.scrollHeight, behavior: "smooth" });
      window.setTimeout(() => composerRef.current?.focus(), 350);
    });
  };
  const goHome = () => {
    setMobileDrawerOpen(false);
    setIsHome(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const startHomeSuggestion = (query: string) => {
    const nextSessionId = newSessionId();
    localStorage.setItem("shopcross.session", nextSessionId);
    // 先立刻进入会话页，给点击明确反馈；请求随后使用这个新会话发送。
    setTurns([{ role: "buyer", text: query }]); setEvents([]); setStreaming(""); setOrderNotice("");
    setSessionId(nextSessionId);
    setIsHome(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
    window.setTimeout(() => void submit(query, nextSessionId, true), 0);
  };

  return (
    <div className="app-shell">
      {!identity ? (
        <main className="auth-page">
          <div className="auth-layout">
            <section className="auth-intro">
              <a className="brand-lockup" href="/"><span className="brand-symbol">G</span><span><b>AI ShopCross Assistant</b><small>CROSS-BORDER COMMERCE</small></span></a>
              <div className="auth-intro-copy"><span className="eyebrow">PRIVATE COMMERCE WORKSPACE</span><h1>每一次购物决策，<em>只属于你。</em></h1><p>保存跨境选品、到手价计算和执行过程。你的会话与使用记录始终和账号独立绑定。</p></div>
              <div className="auth-benefits"><span><b>01</b> 专属会话与偏好</span><span><b>02</b> 实时 Agent 执行轨迹</span><span><b>03</b> 跨设备安全续接</span></div>
              <div className="auth-orbit orbit-one" /><div className="auth-orbit orbit-two" /><div className="auth-glow" />
            </section>
            <section className="auth-card">
              <div className="auth-card-head"><span className="eyebrow">{authMode === "login" ? "WELCOME BACK" : "GET STARTED"}</span><h2>{authMode === "login" ? "登录工作台" : "创建你的账户"}</h2><p>{authMode === "login" ? "继续你的跨境购物会话。" : "注册后即可拥有独立、私密的购物记录。"}</p></div>
              <form onSubmit={(event) => { event.preventDefault(); void authenticate(); }}>
                <label>账号<span>任意非空内容</span><input value={username} autoComplete="username" onChange={(event) => setUsername(event.target.value)} placeholder="输入账号" required /></label>
                <label>密码<span>任意非空内容</span><input value={password} type="password" autoComplete={authMode === "login" ? "current-password" : "new-password"} onChange={(event) => setPassword(event.target.value)} placeholder="输入密码" required /></label>
                {authError && <p className="auth-error" role="alert">{authError}</p>}
                <button className="auth-submit" disabled={authBusy}><span>{authBusy ? "处理中…" : authMode === "login" ? "登录并继续" : "创建账户"}</span><i>→</i></button>
              </form>
              <div className="auth-divider"><span>安全的个人工作台</span></div>
              <button className="auth-switch" onClick={() => { setAuthMode((mode) => mode === "login" ? "register" : "login"); setAuthError(""); }}>
                {authMode === "login" ? "还没有账号？" : "已经有账号？"}<b>{authMode === "login" ? "注册" : "登录"} →</b>
              </button>
            </section>
          </div>
        </main>
      ) : <>
      <header className="topbar">
        <button className="mobile-menu-button" onClick={() => setMobileDrawerOpen((open) => !open)} aria-label={mobileDrawerOpen ? "收起会话记录" : "打开会话记录"} aria-expanded={mobileDrawerOpen}>☰</button>
        <a className="brand-lockup" href="/" onClick={(event) => { event.preventDefault(); goHome(); }} aria-label="AI ShopCross Assistant 首页">
          <span className="brand-symbol">G</span>
          <span><b>AI ShopCross Assistant</b><small>CROSS-BORDER COMMERCE</small></span>
        </a>
        <div className="topbar-center"><span className="model-pill"><i /> LongCat-2.0</span><span className="divider" /><span>向量检索 · 到手价计算 · 智能选品</span></div>
        <div className={`connection ${connected ? "online" : "offline"}`}><i />{connected ? "服务在线" : "正在重连"}</div>
      </header>

      <div className={`mobile-history-layer ${mobileDrawerOpen ? "open" : ""}`} onClick={() => setMobileDrawerOpen(false)}>
        <aside className="mobile-history-drawer" onClick={(event) => event.stopPropagation()}>
          <div className="mobile-drawer-head"><div><span className="eyebrow">CONVERSATIONS</span><h2>会话记录</h2></div><button onClick={() => setMobileDrawerOpen(false)} aria-label="关闭会话记录">×</button></div>
          <button className="new-session" onClick={createSession}>＋ 新开会话</button>
          <div className="session-list">
            {sessions.length === 0 && <p className="session-empty">暂无历史会话</p>}
            {sessions.map((session) => <button key={session.session_id} className={`session-item ${session.session_id === sessionId ? "active" : ""}`} onClick={() => switchSession(session.session_id)}><span>✦</span><div><b>{sessionLabel(session.session_id)}</b><small>{session.last_active_at ? new Date(session.last_active_at).toLocaleString() : "刚刚"}</small></div></button>)}
          </div>
        </aside>
      </div>

      <div className={`workspace ${isHome ? "home-workspace" : "session-workspace"}`}>
        <aside className="history-rail">
          <div className="history-head"><div><span className="eyebrow">CONVERSATIONS</span><h2>会话记录</h2></div><button className="new-session" onClick={createSession}>＋ 新开会话</button></div>
          <div className="session-list">
            {sessions.length === 0 && <p className="session-empty">暂无历史会话</p>}
            {sessions.map((session) => <button key={session.session_id} className={`session-item ${session.session_id === sessionId ? "active" : ""}`} onClick={() => switchSession(session.session_id)}><span>✦</span><div><b>{sessionLabel(session.session_id)}</b><small>{session.last_active_at ? new Date(session.last_active_at).toLocaleString() : "刚刚"}</small></div></button>)}
          </div>
        </aside>
        <main className="commerce-panel">
          <section ref={conversationRef} className={`conversation ${isHome ? "home-view" : "session-view"}`}>
            {isHome ? (
              <div className="welcome">
                <span className="eyebrow">CROSS-BORDER SHOPPING AGENT</span>
                <h1>跨境购物，先算清楚<br /><em>真正的到手价</em></h1>
                <p>用自然语言描述需求。AI ShopCross Assistant 会完成商品召回、价格筛选和关税运费估算。</p>
                <div className="capability-row"><span>60+ 商品向量索引</span><span>多 Agent 协作</span><span>实时执行轨迹</span></div>
                <div className="suggestions">
                  {SUGGESTIONS.map((item) => (
                    <button type="button" key={item.label} onClick={() => void startHomeSuggestion(item.text)}>
                      <span className="suggestion-icon">{item.icon}</span><span><b>{item.label}</b><small>{item.text}</small></span><span className="arrow">↗</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
              <div className="conversation-header">
                <div><span className="eyebrow">LIVE CONVERSATION</span><h2>智能购物会话</h2></div>
                <div className="session-stats"><span>{toolCount} 次工具调用</span><span>{events.length} 个实时事件</span><button className="mobile-new-session" onClick={createSession}>＋ 新会话</button></div>
              </div>

              {orderNotice && <div className="order-notice" role="status">✓ {orderNotice}</div>}

              <div className="turns" ref={turnsRef}>
              {!hasConversation && <div className="session-prompts"><span>可以这样问</span>{SUGGESTIONS.map((item) => <button key={item.label} onClick={() => void submit(item.text)}>{item.text}</button>)}</div>}
              {(() => {
                let agentReplyIndex = 0;
                return turns.map((turn, index) => {
                  const groups = turn.role === "agent" ? replyRecommendations[agentReplyIndex++] ?? [] : [];
                  return (
                    <Fragment key={index}>
                      <article className={`turn ${turn.role}`}>
                        <div className="avatar">{turn.role === "buyer" ? "你" : <AssistantMark />}</div>
                        <div className="message-wrap"><div className="message-meta">{turn.role === "buyer" ? "你的需求" : "AI ShopCross Assistant"}</div><div className="message-text">{turn.text}</div></div>
                      </article>
                      {turn.role === "agent" && <ProductCards groups={groups} busy={busy} onOrder={beginOrder} />}
                    </Fragment>
                  );
                });
              })()}
              {streaming && (
                <article className="turn agent streaming"><div className="avatar"><AssistantMark /></div><div className="message-wrap"><div className="message-meta">正在生成</div><div className="message-text">{streaming}<span className="cursor" /></div></div></article>
              )}
              {busy && !streaming && <div className="thinking"><span /><span /><span /> Agent 正在检索和计算，请稍候</div>}
              </div>

              <section className="composer-zone">
              <div className="composer">
                <span className="spark">✦</span>
                <textarea ref={composerRef} value={input} rows={1} placeholder="描述商品、预算和收货国家，例如：250 美元的降噪耳机寄到美国……" onChange={(event) => { setInput(event.target.value); event.currentTarget.style.height = "auto"; event.currentTarget.style.height = `${event.currentTarget.scrollHeight}px`; }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} />
                <button className="send-button" onClick={() => void submit()} disabled={busy || !input.trim()} aria-label="发送">{busy ? <span className="spinner" /> : "↑"}</button>
              </div>
              <div className="composer-foot"><span>Enter 发送 · Shift + Enter 换行</span><span>价格为估算值，以最终结算为准</span></div>
              </section>
              </>
            )}
          </section>
        </main>

        <aside className="insight-rail">
          <div className="rail-intro"><span className="eyebrow">AGENT OBSERVABILITY</span><h2>执行轨迹</h2><p>实时查看 Agent 如何理解、检索与计算，结果可追溯。</p></div>
          <EventTimeline events={events} />
          <div className="session-card"><span>当前会话</span><code>{sessionId}</code><span>当前账号</span><code>{identity?.username ?? "未登录"}</code></div>
        </aside>
      </div>
      </>}
    </div>
  );
}
