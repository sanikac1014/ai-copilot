import { useEffect, useRef, useState } from "react";

export default function ChatPanel({ chat, onSend, onReset, loading }) {
  const [message, setMessage] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat, loading]);

  const submit = () => {
    if (!message.trim() || loading) return;
    onSend(message);
    setMessage("");
  };

  const last = chat[chat.length - 1];
  // Typing indicator shows while loading and no assistant bubble exists yet (waiting for first token).
  const typingAfterUser = Boolean(loading && last?.role === "user");

  return (
    <div className="flex h-full min-h-[70vh] flex-col rounded-xl border border-blue-900/35 bg-slate-900/90 p-4 shadow-lg shadow-blue-950/20">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold tracking-tight text-white">Chat</h2>
        <button
          type="button"
          onClick={onReset}
          disabled={loading || chat.length === 0}
          className="rounded-lg border border-slate-700/80 bg-slate-800/80 px-3 py-1 text-xs font-medium text-slate-400 transition-all duration-200 hover:border-red-500/40 hover:text-red-300 disabled:pointer-events-none disabled:opacity-30"
        >
          Reset Chat
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        {chat.length === 0 && !loading ? (
          <p className="rounded-lg border border-dashed border-slate-700/80 bg-slate-950/50 px-4 py-8 text-center text-sm text-slate-500 transition-colors duration-200">
            Click a suggestion or type a question below — answers stream in as the assistant &quot;types&quot;.
          </p>
        ) : null}
        {chat.map((msg, idx) => (
          <div
            key={`${idx}-${msg.role}`}
            className={`rounded-lg border px-3 py-2.5 text-sm transition-all duration-200 ${
              msg.role === "user"
                ? "border-blue-800/40 bg-blue-950/40 text-white shadow-sm"
                : "border-slate-700/80 bg-slate-800/90 text-slate-100 shadow-inner"
            }`}
          >
            <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              <span>{msg.role === "user" ? "You" : "Assistant"}</span>
              {msg.timestamp && <span className="font-normal normal-case tracking-normal opacity-70">{msg.timestamp}</span>}
            </div>
            <div className="whitespace-pre-wrap leading-relaxed">
              {msg.content}
              {loading && msg.role === "assistant" && msg.streaming && msg.content ? (
                <span
                  className="ml-0.5 inline-block h-4 w-0.5 animate-pulse rounded-sm bg-orange-400 align-middle"
                  aria-hidden
                />
              ) : null}
            </div>
          </div>
        ))}
        {typingAfterUser && (
          <div className="flex items-center gap-2 rounded-lg border border-orange-500/25 bg-slate-800/80 px-3 py-2.5 text-sm text-slate-300 shadow-sm">
            <span className="font-medium text-slate-200">Assistant is typing</span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-1.5 w-1.5 animate-typing-dot rounded-full bg-orange-400 [animation-delay:0ms]" />
              <span className="inline-block h-1.5 w-1.5 animate-typing-dot rounded-full bg-orange-400 [animation-delay:0.15s]" />
              <span className="inline-block h-1.5 w-1.5 animate-typing-dot rounded-full bg-orange-400 [animation-delay:0.3s]" />
            </span>
          </div>
        )}
        <div ref={bottomRef} className="h-px w-full shrink-0" aria-hidden />
      </div>
      <div className="mt-4 flex gap-2 border-t border-slate-800/80 pt-3">
        <input
          value={message}
          disabled={loading}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white shadow-inner transition-all duration-200 placeholder:text-slate-600 focus:border-orange-400/50 focus:outline-none focus:ring-2 focus:ring-blue-600/40 disabled:opacity-50"
          placeholder="Ask anything…"
        />
        <button
          type="button"
          onClick={submit}
          disabled={loading || !message.trim()}
          className="rounded-lg border border-blue-600/50 bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-md transition-all duration-200 hover:border-orange-400 hover:bg-blue-500 hover:shadow-[0_0_16px_rgba(249,115,22,0.35)] active:scale-[0.97] disabled:pointer-events-none disabled:opacity-40"
        >
          {loading ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
