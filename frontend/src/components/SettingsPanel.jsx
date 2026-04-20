import { useEffect, useState } from "react";

const DEFAULTS = {
  groq_api_key: "",
  model_primary: "openai/gpt-oss-120b",
  model_fallback: "llama-3.3-70b-versatile",
  suggestion_context_chars: 1200,
  chat_context_chars: 4000,
  suggestion_prompt_extra: "",
  chat_prompt_extra: "",
};

export default function SettingsPanel({ isOpen, onClose, onSaved, apiUrl }) {
  const [cfg, setCfg] = useState(DEFAULTS);
  const [showKey, setShowKey] = useState(false);
  const [status, setStatus] = useState(null); // null | "saving" | "saved" | "error"

  useEffect(() => {
    if (!isOpen) return;
    try {
      const stored = localStorage.getItem("twinmind_config");
      if (stored) setCfg({ ...DEFAULTS, ...JSON.parse(stored) });
      else setCfg(DEFAULTS);
    } catch {
      setCfg(DEFAULTS);
    }
  }, [isOpen]);

  const set = (key, val) => setCfg((prev) => ({ ...prev, [key]: val }));

  const save = async () => {
    setStatus("saving");
    try {
      localStorage.setItem("twinmind_config", JSON.stringify(cfg));
      const res = await fetch(`${apiUrl}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      if (!res.ok) throw new Error(await res.text());
      setStatus("saved");
      onSaved(cfg);
      setTimeout(() => setStatus(null), 2000);
    } catch {
      setStatus("error");
      setTimeout(() => setStatus(null), 3000);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-xl border border-blue-800/50 bg-slate-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-white">Settings</h2>
            <p className="text-xs text-slate-400 mt-0.5">Configure API key, models, and prompt behaviour</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 hover:border-slate-600 hover:text-white transition-colors"
          >
            Close
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto px-5 py-4 space-y-5">
          {/* API Key */}
          <section>
            <label className="block text-xs font-semibold uppercase tracking-widest text-orange-300 mb-2">
              Groq API Key
            </label>
            <div className="flex gap-2">
              <input
                type={showKey ? "text" : "password"}
                value={cfg.groq_api_key}
                onChange={(e) => set("groq_api_key", e.target.value)}
                placeholder="gsk_..."
                className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-orange-400/60 focus:outline-none focus:ring-2 focus:ring-blue-600/30"
              />
              <button
                type="button"
                onClick={() => setShowKey((s) => !s)}
                className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-400 hover:text-white transition-colors"
              >
                {showKey ? "Hide" : "Show"}
              </button>
            </div>
            <p className="mt-1.5 text-[11px] text-slate-500">
              Get yours at{" "}
              <span className="text-blue-400">console.groq.com</span>. Never stored server-side between sessions.
            </p>
          </section>

          {/* Models */}
          <section>
            <label className="block text-xs font-semibold uppercase tracking-widest text-blue-300 mb-2">
              Models
            </label>
            <div className="space-y-2">
              <div>
                <p className="text-[11px] text-slate-400 mb-1">Primary (suggestions + chat)</p>
                <input
                  type="text"
                  value={cfg.model_primary}
                  onChange={(e) => set("model_primary", e.target.value)}
                  placeholder="openai/gpt-oss-120b"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-blue-500/50 focus:outline-none"
                />
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-1">Fallback (on rate limit)</p>
                <input
                  type="text"
                  value={cfg.model_fallback}
                  onChange={(e) => set("model_fallback", e.target.value)}
                  placeholder="llama-3.3-70b-versatile"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-blue-500/50 focus:outline-none"
                />
              </div>
            </div>
          </section>

          {/* Context Windows */}
          <section>
            <label className="block text-xs font-semibold uppercase tracking-widest text-blue-300 mb-2">
              Context Windows
            </label>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-[11px] text-slate-400 mb-1">Suggestions (chars)</p>
                <input
                  type="number"
                  min={200}
                  max={8000}
                  value={cfg.suggestion_context_chars}
                  onChange={(e) => set("suggestion_context_chars", Number(e.target.value))}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-blue-500/50 focus:outline-none"
                />
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-1">Chat (chars)</p>
                <input
                  type="number"
                  min={500}
                  max={16000}
                  value={cfg.chat_context_chars}
                  onChange={(e) => set("chat_context_chars", Number(e.target.value))}
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-blue-500/50 focus:outline-none"
                />
              </div>
            </div>
          </section>

          {/* Prompt Extras */}
          <section>
            <label className="block text-xs font-semibold uppercase tracking-widest text-blue-300 mb-2">
              Extra Prompt Instructions
            </label>
            <div className="space-y-3">
              <div>
                <p className="text-[11px] text-slate-400 mb-1">Appended to suggestion prompt</p>
                <textarea
                  rows={3}
                  value={cfg.suggestion_prompt_extra}
                  onChange={(e) => set("suggestion_prompt_extra", e.target.value)}
                  placeholder="e.g. Focus on cost-reduction angles. Prioritize questions the speaker seems stuck on."
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-blue-500/50 focus:outline-none resize-none"
                />
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-1">Appended to chat prompt</p>
                <textarea
                  rows={3}
                  value={cfg.chat_prompt_extra}
                  onChange={(e) => set("chat_prompt_extra", e.target.value)}
                  placeholder="e.g. Always end with a concrete next action. Cite specific things the user said."
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-blue-500/50 focus:outline-none resize-none"
                />
              </div>
            </div>
          </section>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-slate-800 px-5 py-3">
          <p className="text-[11px] text-slate-500">
            {status === "saved" && <span className="text-emerald-400">✓ Saved and applied</span>}
            {status === "error" && <span className="text-red-400">Failed to apply — check server</span>}
            {status === "saving" && <span className="text-blue-300">Saving…</span>}
            {!status && "Changes apply immediately to all future requests."}
          </p>
          <button
            type="button"
            onClick={save}
            disabled={status === "saving"}
            className="rounded-lg border border-orange-500/50 bg-orange-500/90 px-4 py-2 text-sm font-semibold text-white shadow-md transition-all duration-200 hover:bg-orange-400 active:scale-[0.97] disabled:opacity-50"
          >
            {status === "saving" ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
