import { useEffect, useMemo, useRef, useState } from "react";

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

function SectionDivider({ label, color = "text-slate-500" }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-px flex-1 bg-slate-700/50" />
      <span className={`text-[10px] font-semibold uppercase tracking-widest ${color}`}>{label}</span>
      <div className="h-px flex-1 bg-slate-700/50" />
    </div>
  );
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

  // Stable display state — the ONLY source of truth for rendering.
  // Stored as {batch_id, segment_id, suggestions[]} sorted newest-first.
  const [stableBatches, setStableBatches] = useState([]);
  const renderedBatchIdRef = useRef(null);

  const focusKey = context?.primary_focus ?? "";

  // ── Phrase cycling while loading ─────────────────────────────────────────
  useEffect(() => {
    if (!loading) return undefined;
    const id = window.setInterval(() => setPhraseIdx((i) => (i + 1) % THINKING_PHRASES.length), 2400);
    return () => window.clearInterval(id);
  }, [loading]);

  // ── Context label pulse ───────────────────────────────────────────────────
  useEffect(() => {
    if (!focusKey) return;
    setAnimateContext(true);
    const t = window.setTimeout(() => setAnimateContext(false), 700);
    return () => window.clearTimeout(t);
  }, [focusKey]);

  // ── Stable batches: update only when latestBatchId changes ───────────────
  useEffect(() => {
    if (!Array.isArray(suggestionHistory) || suggestionHistory.length === 0) {
      // Fallback: if no history yet, show the suggestions prop as a single batch.
      if (suggestions && suggestions.length > 0) {
        setStableBatches([{ batch_id: latestBatchId, segment_id: currentSegmentId, suggestions }]);
        renderedBatchIdRef.current = latestBatchId;
      }
      return;
    }

    const currentBatches = suggestionHistory.filter((b) => b.segment_id === currentSegmentId);
    console.log(
      `[SuggestionsPanel] segment=${currentSegmentId} matching=${currentBatches.length}/${suggestionHistory.length}`
    );

    if (currentBatches.length === 0) return;

    const latest = currentBatches[currentBatches.length - 1];

    // Skip re-render if the newest batch hasn't changed.
    if (renderedBatchIdRef.current === latest.batch_id) return;

    renderedBatchIdRef.current = latest.batch_id;

    // Sort newest-first so index 0 is always the latest batch.
    const sorted = [...currentBatches].sort((a, b) => b.batch_id - a.batch_id);
    setStableBatches(sorted);
  }, [suggestionHistory, currentSegmentId, latestBatchId, suggestions]);

  // ── Stagger slide-in — keyed only to latestBatchId, not suggestions prop ─
  useEffect(() => {
    if (loading || stableBatches.length === 0) {
      setStaggerOn(false);
      return undefined;
    }
    setStaggerOn(false);
    const raf = requestAnimationFrame(() => setStaggerOn(true));
    return () => cancelAnimationFrame(raf);
  }, [loading, latestBatchId]);

  // ── Glow pulse on new batch ───────────────────────────────────────────────
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
    return raw.length <= 22 ? raw : `${raw.slice(0, 20)}…`;
  };

  const renderSuggestionCard = (item, idx, isLatest, batchId) => {
    const aura = confidenceAura(item.score);
    const delayMs = idx * 150;
    return (
      <button
        key={`${batchId}-${idx}-${item.preview?.slice(0, 24)}`}
        type="button"
        className={[
          "group w-full rounded-lg border bg-slate-900/80 p-3 text-left ring-inset",
          "transition-all duration-200 ease-out hover:-translate-y-1 hover:shadow-lg active:scale-[0.99]",
          isLatest ? "opacity-100" : "opacity-50",
          aura.ring,
          aura.shadow,
          idx === 0 && isLatest
            ? "border-blue-500/50 hover:border-orange-400/60 hover:shadow-blue-900/30"
            : "border-slate-700/90 hover:border-blue-500/40",
          isLatest && glowBatch && idx === 0 ? "animate-card-glow" : "",
          isLatest && staggerOn ? "translate-x-0 opacity-100" : isLatest ? "translate-x-5 opacity-0" : "",
        ]
          .filter(Boolean)
          .join(" ")}
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
            <span className={`rounded-md px-2 py-0.5 text-[10px] font-semibold tracking-wide ${getBadgeClass(item)}`}>
              {getBadgeLabel(item)}
            </span>
            <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{aura.label}</span>
          </div>
          {isLatest && latestBatchId > 0 && idx < 3 ? (
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

  const hasStable = stableBatches.length > 0;

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

      {/* Scrollable suggestion list — rendered from stableBatches only */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-0.5">
        {/* Shimmer: only while loading AND nothing stable to show yet */}
        {loading && !hasStable ? (
          <div className="space-y-3">
            <ShimmerCard />
            <ShimmerCard />
            <ShimmerCard />
          </div>
        ) : null}

        {stableBatches.map((batch, batchIndex) => {
          const isLatest = batchIndex === 0;
          const batchSuggestions = batch.suggestions || [];
          if (batchSuggestions.length === 0) return null;
          return (
            <div key={batch.batch_id} className={`space-y-3 transition-opacity duration-300 ${loading && isLatest ? "opacity-45" : "opacity-100"}`}>
              {isLatest ? (
                stableBatches.length > 1 && (
                  <SectionDivider label="Latest Suggestions" color="text-orange-400/80" />
                )
              ) : (
                batchIndex === 1 && <SectionDivider label="Previous Suggestions" color="text-slate-500" />
              )}
              {batchSuggestions.map((item, idx) => renderSuggestionCard(item, idx, isLatest, batch.batch_id))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
