import { useEffect, useMemo, useState } from "react";

const THINKING_PHRASES = [
  "Analyzing your context…",
  "Identifying bottlenecks…",
  "Evaluating tradeoffs…",
  "Mapping decisions to your transcript…",
  "Surfacing angles you might miss…",
];

function ShimmerCard() {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-700/80 bg-slate-800/50 p-3">
      <div className="h-3 w-24 rounded bg-gradient-to-r from-slate-700 via-slate-500 to-slate-700 bg-[length:200%_100%] animate-shimmer" />
      <div className="mt-3 h-4 w-full rounded bg-gradient-to-r from-slate-700 via-slate-500 to-slate-700 bg-[length:200%_100%] animate-shimmer" />
      <div className="mt-2 h-4 w-[85%] rounded bg-gradient-to-r from-slate-700 via-slate-500 to-slate-700 bg-[length:200%_100%] animate-shimmer" />
    </div>
  );
}

function confidenceAura(score) {
  if (score == null || Number.isNaN(score)) {
    return { ring: "ring-1 ring-slate-600/50", shadow: "", label: "—" };
  }
  if (score >= 0.68) {
    return {
      ring: "ring-2 ring-emerald-500/50",
      shadow: "shadow-[0_0_20px_rgba(16,185,129,0.22)]",
      label: "Strong fit",
    };
  }
  if (score >= 0.52) {
    return {
      ring: "ring-2 ring-amber-400/45",
      shadow: "shadow-[0_0_16px_rgba(251,191,36,0.18)]",
      label: "Moderate fit",
    };
  }
  return { ring: "ring-1 ring-slate-600/60", shadow: "", label: "Exploratory" };
}

export default function SuggestionsPanel({
  suggestions,
  suggestionHistory,
  currentSegmentId,
  onSuggestionClick,
  onRefresh,
  loading,
  context,
  latestBatchId,
  newBatchPulse,
  topicShiftLabel,
}) {
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [animateContext, setAnimateContext] = useState(false);
  const [staggerOn, setStaggerOn] = useState(false);
  const [glowBatch, setGlowBatch] = useState(false);

  const focusKey = context?.primary_focus ?? "";

  useEffect(() => {
    if (!loading) return undefined;
    const id = window.setInterval(() => {
      setPhraseIdx((i) => (i + 1) % THINKING_PHRASES.length);
    }, 2400);
    return () => window.clearInterval(id);
  }, [loading]);

  useEffect(() => {
    if (!focusKey) return;
    setAnimateContext(true);
    const t = window.setTimeout(() => setAnimateContext(false), 700);
    return () => window.clearTimeout(t);
  }, [focusKey]);

  useEffect(() => {
    if (loading || !suggestions.length) {
      setStaggerOn(false);
      return undefined;
    }
    setStaggerOn(false);
    const raf = requestAnimationFrame(() => setStaggerOn(true));
    return () => cancelAnimationFrame(raf);
  }, [loading, latestBatchId, suggestions]);

  useEffect(() => {
    if (!newBatchPulse) return;
    setGlowBatch(true);
    const t = window.setTimeout(() => setGlowBatch(false), 820);
    return () => window.clearTimeout(t);
  }, [newBatchPulse, latestBatchId]);

  const labelByIntent = useMemo(
    () => ({
      root_cause: "🔍 Probe",
      system_design: "🧠 Insight",
      tradeoff: "⚖️ Tradeoff",
      validation: "✅ Validate",
      constraint: "🚀 Optimization",
      alternative: "🔀 Alternative",
      clarification: "🧠 Insight",
      scope: "🧠 Insight",
    }),
    []
  );

  const badgeByIntent = {
    root_cause: "bg-rose-500/15 text-rose-100 border border-rose-500/25",
    system_design: "bg-emerald-500/15 text-emerald-100 border border-emerald-500/25",
    tradeoff: "bg-amber-500/15 text-amber-100 border border-amber-500/30",
    validation: "bg-sky-500/15 text-sky-100 border border-sky-500/25",
    constraint: "bg-orange-500/15 text-orange-100 border border-orange-500/30",
    alternative: "bg-cyan-500/15 text-cyan-100 border border-cyan-500/25",
    clarification: "bg-blue-500/15 text-blue-100 border border-blue-500/25",
    scope: "bg-slate-500/15 text-slate-100 border border-slate-500/25",
  };

  const badgeByType = {
    question: "bg-sky-500/15 text-sky-100 border border-sky-500/25",
    clarification: "bg-blue-500/15 text-blue-100 border border-blue-500/25",
    insight: "bg-emerald-500/15 text-emerald-100 border border-emerald-500/25",
    risk: "bg-amber-500/15 text-amber-100 border border-amber-500/30",
    "risk/tradeoff": "bg-amber-500/15 text-amber-100 border border-amber-500/30",
    solution: "bg-violet-500/15 text-violet-100 border border-violet-500/25",
    recommendation: "bg-violet-500/15 text-violet-100 border border-violet-500/25",
  };

  const getBadgeClass = (item) => {
    const ic = (item?.intent_category || "").toLowerCase();
    if (ic && badgeByIntent[ic]) return badgeByIntent[ic];
    const key = (item?.type || "").toLowerCase();
    return badgeByType[key] || "bg-blue-500/10 text-blue-100 border border-blue-500/20";
  };

  const getBadgeLabel = (item) => {
    const ic = (item?.intent_category || "").toLowerCase();
    if (ic && labelByIntent[ic]) return labelByIntent[ic];
    const raw = (item?.type || "Suggestion").trim();
    if (raw.length <= 22) return raw;
    return `${raw.slice(0, 20)}…`;
  };

  // Strict segment filtering — ONLY render batches for the active segment.
  const segmentBatches = useMemo(() => {
    if (!Array.isArray(suggestionHistory) || suggestionHistory.length === 0) return [];
    const filtered = suggestionHistory.filter((b) => b.segment_id === currentSegmentId);
    console.log(
      `[SuggestionsPanel] segment=${currentSegmentId} matching_batches=${filtered.length}/${suggestionHistory.length}`
    );
    return filtered;
  }, [suggestionHistory, currentSegmentId]);

  const previousBatches = segmentBatches.slice(0, -1);
  // Use history as source of truth; fall back to suggestions prop ONLY for the very first
  // render before any history arrives (history will be empty, segment will be 0).
  const latestBatchSuggestions =
    segmentBatches.length > 0
      ? segmentBatches[segmentBatches.length - 1].suggestions || []
      : suggestionHistory.length === 0
      ? suggestions
      : [];

  const renderSuggestionCard = ({ item, idx, isLatest, batchIdx }) => {
    const aura = confidenceAura(item.score);
    const delayMs = idx * 150;
    const isNew = isLatest;
    return (
      <button
        key={`${batchIdx}-${idx}-${item.preview?.slice(0, 24)}`}
        type="button"
        className={`group w-full rounded-lg border bg-slate-900/80 p-3 text-left ring-inset transition-all duration-200 ease-out hover:-translate-y-1 hover:shadow-lg active:scale-[0.99] ${
          isLatest ? "opacity-100" : "opacity-50"
        } ${aura.ring} ${aura.shadow} ${
          idx === 0 && isLatest
            ? "border-blue-500/50 hover:border-orange-400/60 hover:shadow-blue-900/30"
            : "border-slate-700/90 hover:border-blue-500/40"
        } ${isLatest && glowBatch && idx === 0 ? "animate-card-glow" : ""} ${
          isLatest && staggerOn ? "translate-x-0 opacity-100" : isLatest ? "translate-x-5 opacity-0" : ""
        }`}
        style={
          isLatest
            ? {
                transitionDelay: `${delayMs}ms`,
                transitionProperty: "transform, opacity, box-shadow, border-color",
                transitionDuration: "380ms",
              }
            : {}
        }
        onClick={() => onSuggestionClick(item)}
        title={`Why this suggestion: ${item.reason || "High relevance to current focus"}`}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span
              className={`rounded-md px-2 py-0.5 text-[10px] font-semibold tracking-wide ${getBadgeClass(item)}`}
            >
              {getBadgeLabel(item)}
            </span>
            <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{aura.label}</span>
          </div>
          {isNew && latestBatchId > 0 && idx < 3 ? (
            <span
              className={`rounded-md border border-orange-500/40 bg-orange-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-orange-200 transition-all duration-200 ${
                newBatchPulse ? "animate-pulse shadow-[0_0_12px_rgba(249,115,22,0.45)]" : ""
              }`}
            >
              New
            </span>
          ) : null}
        </div>
        <div className="mt-2 text-sm font-medium leading-snug text-white">{item.preview}</div>
        <div className="mt-1.5 text-xs leading-relaxed text-slate-400">{item.reason}</div>
        {isLatest && (
          <div className="mt-2 flex items-center gap-2 text-[10px] text-slate-500">
            <span
              className={`h-1.5 flex-1 max-w-[4rem] rounded-full ${
                (item.score ?? 0) >= 0.68
                  ? "bg-emerald-500/70"
                  : (item.score ?? 0) >= 0.52
                  ? "bg-amber-400/70"
                  : "bg-slate-600"
              }`}
            />
            <span className="tabular-nums">
              Rel {item.relevance ?? "—"} · Nov {item.novelty ?? "—"} · Act {item.actionability ?? "—"}
            </span>
          </div>
        )}
      </button>
    );
  };

  return (
    <div className="relative flex h-full flex-col overflow-hidden rounded-xl border border-blue-900/35 bg-slate-900/90 p-4 shadow-lg shadow-blue-950/20 backdrop-blur-sm transition-colors duration-200">
      {topicShiftLabel ? (
        <div
          key={topicShiftLabel}
          className="mb-3 rounded-lg border border-orange-500/45 bg-gradient-to-r from-orange-500/15 to-amber-500/10 px-3 py-2.5 text-sm text-orange-50 shadow-[0_0_24px_rgba(249,115,22,0.2)] animate-context-nudge"
          role="status"
        >
          🧠 New topic detected: <span className="font-semibold text-white">{topicShiftLabel}</span>
        </div>
      ) : null}
      <div className="mb-4 flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold tracking-tight text-white">Live Suggestions</h2>
        <button
          type="button"
          className="rounded-lg border border-blue-600/40 bg-blue-600/90 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all duration-200 hover:border-orange-400 hover:bg-blue-500 hover:shadow-[0_0_14px_rgba(249,115,22,0.35)] active:scale-[0.97]"
          onClick={onRefresh}
        >
          Refresh
        </button>
      </div>

      <div
        className={`mb-3 rounded-lg border border-blue-800/40 bg-gradient-to-r from-slate-800/90 to-slate-900/90 px-3 py-2 text-sm text-slate-200 transition-all duration-200 ${
          animateContext ? "animate-context-nudge ring-1 ring-orange-400/30" : ""
        }`}
      >
        <div className="font-medium text-slate-100">
          🧠 AI is tracking: <span className="text-orange-200">{context?.primary_focus || "…"}</span>
        </div>
        {context && (
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
            <span className="text-blue-300/90">Type: {context.conversation_type}</span>
            <span>Stage: {context.stage}</span>
            {context.secondary_topics?.[0] ? <span>Also: {context.secondary_topics[0]}</span> : null}
          </div>
        )}
      </div>

      {loading && (
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-blue-200/90">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-orange-400 opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-orange-500" />
          </span>
          <span className="transition-opacity duration-300">{THINKING_PHRASES[phraseIdx]}</span>
        </div>
      )}

      {/* Scrollable suggestion area */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-0.5">
        {loading && !suggestions.length ? (
          <div className="space-y-3">
            <ShimmerCard />
            <ShimmerCard />
            <ShimmerCard />
          </div>
        ) : null}

        {/* Previous suggestion batches */}
        {previousBatches.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="h-px flex-1 bg-slate-700/50" />
              <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                Previous Suggestions
              </span>
              <div className="h-px flex-1 bg-slate-700/50" />
            </div>
            {previousBatches.map((batch, batchIdx) =>
              (batch.suggestions || []).map((item, idx) =>
                renderSuggestionCard({ item, idx, isLatest: false, batchIdx })
              )
            )}
          </div>
        )}

        {/* Latest suggestion batch */}
        {latestBatchSuggestions.length > 0 && (
          <div className={`space-y-3 transition-opacity duration-300 ${loading && suggestions.length ? "opacity-45" : "opacity-100"}`}>
            {previousBatches.length > 0 && (
              <div className="flex items-center gap-2">
                <div className="h-px flex-1 bg-slate-700/50" />
                <span className="text-[10px] font-semibold uppercase tracking-widest text-orange-400/80">
                  Latest Suggestions
                </span>
                <div className="h-px flex-1 bg-slate-700/50" />
              </div>
            )}
            {latestBatchSuggestions.map((item, idx) =>
              renderSuggestionCard({ item, idx, isLatest: true, batchIdx: "latest" })
            )}
          </div>
        )}
      </div>
    </div>
  );
}
