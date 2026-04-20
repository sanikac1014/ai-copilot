import { useEffect, useRef, useState } from "react";

function MicIcon({ pulsing }) {
  return (
    <span className="relative flex h-5 w-5 items-center justify-center">
      {pulsing && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-50" />
      )}
      <svg
        viewBox="0 0 24 24"
        fill="currentColor"
        className="relative h-4 w-4"
        aria-hidden
      >
        <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm6.364 9.364a.75.75 0 0 1 .75.75A7.002 7.002 0 0 1 12.75 17.93V20h2.5a.75.75 0 0 1 0 1.5h-6.5a.75.75 0 0 1 0-1.5h2.5v-2.07A7.002 7.002 0 0 1 4.886 11.114a.75.75 0 0 1 1.5 0 5.5 5.5 0 0 0 11 0 .75.75 0 0 1 .978-.69z" />
      </svg>
    </span>
  );
}

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, "0");
  const s = (seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export default function TranscriptPanel({
  transcript,
  finalTranscript,
  previewText,
  isRecording,
  onToggleRecording,
}) {
  const [elapsed, setElapsed] = useState(0);
  const bottomRef = useRef(null);

  const speechSupported =
    typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

  useEffect(() => {
    if (!isRecording) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const id = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, [isRecording]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [transcript, previewText]);

  const hasContent = finalTranscript || previewText || transcript.length > 0;

  return (
    <div className="h-full rounded-xl border border-blue-900/35 bg-slate-900/90 p-4 shadow-lg shadow-blue-950/20">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold tracking-tight text-white">Transcript</h2>
          {isRecording && (
            <span className="flex items-center gap-1.5 rounded-md border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-xs font-semibold tabular-nums text-red-300">
              <span className="h-1.5 w-1.5 rounded-full bg-red-400 animate-pulse" />
              {formatDuration(elapsed)}
            </span>
          )}
        </div>
        <button
          type="button"
          className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-semibold shadow-md transition-all duration-200 active:scale-[0.97] ${
            isRecording
              ? "border border-red-400/40 bg-red-600 text-white hover:bg-red-500"
              : "border border-blue-500/40 bg-blue-600 text-white hover:border-orange-400 hover:shadow-[0_0_14px_rgba(249,115,22,0.35)]"
          }`}
          onClick={onToggleRecording}
        >
          <MicIcon pulsing={isRecording} />
          {isRecording ? "Stop" : "Start"}
        </button>
      </div>

      <div className="h-[80vh] space-y-3 overflow-auto">
        <div
          className={`min-h-[3rem] rounded-lg border bg-slate-950 p-3 text-sm leading-relaxed text-slate-100 transition-all duration-200 ${
            isRecording
              ? "border-orange-500/35 shadow-[0_0_0_1px_rgba(249,115,22,0.12)]"
              : "border-slate-700/90"
          }`}
        >
          {hasContent ? (
            <>
              <span>{finalTranscript}</span>
              {finalTranscript && previewText ? <span> </span> : null}
              {previewText ? (
                <span className="text-slate-400 transition-opacity duration-200" aria-live="polite">
                  {previewText}
                </span>
              ) : null}
              {isRecording && !previewText && finalTranscript ? (
                <span className="ml-1 text-slate-500">
                  {!speechSupported ? "Listening… (use Chrome for live preview)" : "Listening…"}
                </span>
              ) : null}
            </>
          ) : isRecording ? (
            <span className="text-slate-500">
              {!speechSupported ? "Listening… (use Chrome for live preview)" : "Listening…"}
            </span>
          ) : (
            <span className="text-slate-600">
              Press <span className="font-semibold text-slate-400">Start</span> and begin speaking — your transcript will appear here.
            </span>
          )}
        </div>

        {transcript.map((item, idx) => (
          <div
            key={`${item.timestamp}-${idx}`}
            className="rounded-lg border border-slate-800/80 bg-slate-800/60 p-2 text-sm text-slate-200 transition-colors duration-200 hover:border-blue-800/50"
          >
            <div className="text-xs text-slate-500">{item.timestamp}</div>
            <div>{item.text}</div>
          </div>
        ))}
        <div ref={bottomRef} className="h-px w-full shrink-0" aria-hidden />
      </div>
    </div>
  );
}
