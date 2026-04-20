import { useEffect, useRef, useState } from "react";
import ChatPanel from "./components/ChatPanel";
import SettingsPanel from "./components/SettingsPanel";
import SuggestionsPanel from "./components/SuggestionsPanel";
import TranscriptPanel from "./components/TranscriptPanel";

const API_URL = import.meta.env.VITE_API_URL || "/api";

function toTimestamp() {
  return new Date().toLocaleTimeString();
}

export default function App() {
  const [transcript, setTranscript] = useState([]);
  /** Whisper-confirmed text only (source of truth for suggestions context display). */
  const [finalTranscript, setFinalTranscript] = useState("");
  /** Browser SpeechRecognition interim / live line (cleared when Whisper chunk arrives). */
  const [previewText, setPreviewText] = useState("");
  const [displayedSuggestions, setDisplayedSuggestions] = useState([]);
  const [suggestionHistory, setSuggestionHistory] = useState([]);
  const [currentSegmentId, setCurrentSegmentId] = useState(0);
  const [chat, setChat] = useState([]);
  const [context, setContext] = useState(null);
  const [isRecording, setIsRecording] = useState(false);
  const [suggestionLoading, setSuggestionLoading] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [latestBatchId, setLatestBatchId] = useState(0);
  const [newBatchPulse, setNewBatchPulse] = useState(false);
  const [topicShiftBanner, setTopicShiftBanner] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false);
  const topicShiftTimerRef = useRef(null);
  const mediaRecorder = useRef(null);
  const chunkBufferRef = useRef([]);
  const stopTimerRef = useRef(null);
  const isRecordingRef = useRef(false);
  const suggestionDebounceRef = useRef(null);
  const autoRefreshRef = useRef(null);
  // Refs so the 30s interval always calls with latest values (avoids stale closure).
  const transcriptRef = useRef([]);
  const postSuggestionsRef = useRef(null);
  const transcribeRequestCounter = useRef(0);
  const speechRecognitionRef = useRef(null);

  // On mount: restore saved config and push to backend.
  useEffect(() => {
    const stored = localStorage.getItem("twinmind_config");
    if (!stored) {
      setSettingsOpen(true); // first visit — open settings immediately
      return;
    }
    try {
      const cfg = JSON.parse(stored);
      if (cfg.groq_api_key) {
        fetch(`${API_URL}/config`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg),
        })
          .then(() => setApiKeyConfigured(true))
          .catch(() => {});
        return;
      }
    } catch {
      // fall through to env-var check
    }
    // No key in localStorage — check if backend already has one via env var.
    fetch(`${API_URL}/config`)
      .then((r) => r.json())
      .then((d) => {
        if (d.groq_api_key_set) {
          setApiKeyConfigured(true); // env var present, no need to prompt
        } else {
          setSettingsOpen(true);
        }
      })
      .catch(() => setSettingsOpen(true));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const flashTopicShiftBanner = (label) => {
    if (topicShiftTimerRef.current) {
      window.clearTimeout(topicShiftTimerRef.current);
    }
    setTopicShiftBanner(label || "New topic");
    topicShiftTimerRef.current = window.setTimeout(() => {
      setTopicShiftBanner(null);
      topicShiftTimerRef.current = null;
    }, 9000);
  };

  useEffect(() => {
    return () => {
      if (topicShiftTimerRef.current) {
        window.clearTimeout(topicShiftTimerRef.current);
      }
    };
  }, []);

  // Keep refs current so the 30s interval callback never closes over stale values.
  transcriptRef.current = transcript;

  const postSuggestions = async (entries) => {
    const startedAt = performance.now();
    setSuggestionLoading(true);
    try {
      const res = await fetch(`${API_URL}/suggestions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript_entries: entries, force_refresh: true }),
      });
      const data = await res.json();
      const incoming = (data.suggestions || []).slice(0, 3);
      const source = data.meta?.suggestion_source;
      console.log("Suggestions received:", incoming.length, "source:", source);
      console.log("Suggestions received (previews):", incoming.map((s) => s.preview).join(" | "));
      console.log("[SUGGESTIONS][HISTORY] length:", (data.suggestion_history || []).length);
      if (source === "llm") {
        console.log("Replacing fallback with real suggestions");
      }
      const newSegmentId = data.current_segment_id ?? 0;
      console.log("CURRENT SEGMENT:", newSegmentId);
      console.log("BATCH SEGMENTS:", (data.suggestion_history || []).map((b) => b.segment_id));

      // On topic shift: clear UI only — history must be preserved for segment filtering.
      if (data.meta?.topic_shift) {
        setDisplayedSuggestions([]);
      }

      // Always overwrite current suggestions with latest batch from API.
      setDisplayedSuggestions(incoming);
      // Update segment id before history so the filter sees the right segment on re-render.
      setCurrentSegmentId(newSegmentId);
      // Append-only: merge new batches by batch_id to avoid duplicates and never reset.
      if (Array.isArray(data.suggestion_history)) {
        setSuggestionHistory((prev) => {
          const existingIds = new Set(prev.map((b) => b.batch_id));
          const toAdd = data.suggestion_history.filter((b) => !existingIds.has(b.batch_id));
          return toAdd.length ? [...prev, ...toAdd] : prev;
        });
      }
      setContext(data.context);
      if (data.meta?.topic_shift) {
        flashTopicShiftBanner(data.context?.primary_focus);
      }
      const batchId = data.meta?.batch_id || 0;
      if (batchId && batchId !== latestBatchId) {
        setNewBatchPulse(true);
        window.setTimeout(() => setNewBatchPulse(false), 1400);
      }
      setLatestBatchId(batchId);

      // Interrupt timing intelligence: pull suggestions earlier when strong conversational signals appear.
      if (data.meta?.early_signal_detected) {
        window.setTimeout(() => postSuggestions(entries), 9000);
      }
      console.log(`[SUGGESTIONS] ${((performance.now() - startedAt) / 1000).toFixed(2)}s`);
    } catch (err) {
      console.error("[SUGGESTIONS][ERROR]", err);
    } finally {
      setSuggestionLoading(false);
    }
  };
  // Always keep the ref pointing at the latest version of the function.
  postSuggestionsRef.current = postSuggestions;

  const sendChat = async (message, fromSuggestion = false) => {
    const userMsg = { role: "user", content: message, timestamp: toTimestamp() };
    setChat((prev) => [...prev, userMsg]);
    setChatLoading(true);
    // No empty bubble here — typing indicator covers the wait; bubble is created on first delta.

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript_entries: transcript, message, from_suggestion: fromSuggestion }),
      });
      if (!res.ok) {
        const errText = await res.text().catch(() => res.statusText);
        throw new Error(errText || `Chat failed (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep any incomplete line for next iteration
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          let evt;
          try { evt = JSON.parse(raw); } catch { continue; }

          if (evt.type === "meta") {
            if (evt.context) setContext(evt.context);
            if (evt.meta?.topic_shift) flashTopicShiftBanner(evt.context?.primary_focus);
          } else if (evt.type === "delta") {
            setChat((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                // Subsequent deltas: append to existing bubble.
                next[next.length - 1] = { ...last, content: last.content + evt.text, streaming: true };
              } else {
                // First delta: create the assistant bubble now (replaces typing indicator).
                next.push({ role: "assistant", content: evt.text, timestamp: "", streaming: true });
              }
              return next;
            });
          } else if (evt.type === "done") {
            setChat((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = { ...last, timestamp: evt.timestamp || toTimestamp(), streaming: false };
              }
              return next;
            });
          }
        }
      }
    } catch (err) {
      console.error("[CHAT]", err);
      const msg = "Could not reach the assistant. Check the API and try again.";
      setChat((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = { ...last, content: msg, streaming: false };
          return next;
        }
        return [...next, { role: "assistant", content: msg, streaming: false }];
      });
    } finally {
      setChatLoading(false);
    }
  };

  const transcribeChunk = async (blob) => {
    const requestId = ++transcribeRequestCounter.current;
    const startedAt = performance.now();
    const formData = new FormData();
    formData.append("file", blob, `chunk-${Date.now()}.webm`);
    try {
      const res = await fetch(`${API_URL}/transcribe`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      const newText = (data.text || data.entry?.text || "").trim();
      if (newText) {
        console.log("NEW CHUNK:", newText);
        setTranscript((prev) => {
          const nextEntry = data.entry || { timestamp: toTimestamp(), text: newText };
          const next = [...prev, nextEntry];
          return next;
        });
        setFinalTranscript((prev) => {
          if (!prev) return newText;
          if (prev.endsWith(newText)) return prev;
          if (newText.startsWith(prev.slice(-Math.min(prev.length, 32)))) {
            return (prev + " " + newText.replace(prev.slice(-Math.min(prev.length, 32)), "")).trim();
          }
          return `${prev} ${newText}`.replace(/\s+/g, " ").trim();
        });
        setPreviewText("");
        console.log("[TRANSCRIBE] success");
      }
      console.log(`[TRANSCRIBE] #${requestId} ${((performance.now() - startedAt) / 1000).toFixed(2)}s`);
    } catch (error) {
      console.error("Transcribe request failed", error);
    }
  };

  const startSpeechPreview = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      return;
    }
    try {
      speechRecognitionRef.current?.stop();
    } catch {
      /* ignore */
    }
    const recognition = new SR();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognition.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (!event.results[i].isFinal) {
          interim += event.results[i][0].transcript;
        }
      }
      setPreviewText(interim.trim());
    };
    recognition.onerror = (e) => {
      console.warn("[SpeechRecognition]", e.error);
      if (e.error === "not-allowed") {
        setPreviewText("Mic permission needed for live preview");
      }
    };
    recognition.onend = () => {
      if (isRecordingRef.current) {
        try {
          recognition.start();
        } catch {
          /* already started */
        }
      }
    };
    speechRecognitionRef.current = recognition;
    try {
      recognition.start();
    } catch (e) {
      console.warn("[SpeechRecognition] start failed", e);
      setPreviewText("Listening…");
    }
  };

  const stopSpeechPreview = () => {
    const rec = speechRecognitionRef.current;
    speechRecognitionRef.current = null;
    if (!rec) return;
    try {
      rec.onend = null;
      rec.stop();
    } catch {
      /* ignore */
    }
    setPreviewText("");
  };

  const startRecording = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const preferredMime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    const recorder = new MediaRecorder(stream, { mimeType: preferredMime });
    mediaRecorder.current = recorder;
    isRecordingRef.current = true;
    chunkBufferRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        chunkBufferRef.current.push(event.data);
      }
    };
    recorder.onstop = async () => {
      if (!chunkBufferRef.current.length) return;
      const blob = new Blob(chunkBufferRef.current, { type: preferredMime });
      chunkBufferRef.current = [];
      if (blob.size > 4000) {
        await transcribeChunk(blob);
      } else {
        console.log(`[TRANSCRIBE] skipped tiny blob ${blob.size} bytes`);
      }
      if (isRecordingRef.current) {
        recorder.start();
        stopTimerRef.current = window.setTimeout(() => recorder.stop(), 30000);
      }
    };
    recorder.start();
    stopTimerRef.current = window.setTimeout(() => recorder.stop(), 30000);
    setIsRecording(true);
    startSpeechPreview();
  };

  const stopRecording = () => {
    isRecordingRef.current = false;
    stopSpeechPreview();
    if (stopTimerRef.current) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    mediaRecorder.current?.stream.getTracks().forEach((track) => track.stop());
    mediaRecorder.current?.stop();
    setIsRecording(false);
  };

  const onToggleRecording = () => {
    if (isRecording) stopRecording();
    else startRecording();
  };

  // Debounce-trigger: fire 2.5 s after each new transcript chunk lands.
  useEffect(() => {
    if (!transcript.length) return undefined;
    if (suggestionDebounceRef.current) {
      window.clearTimeout(suggestionDebounceRef.current);
    }
    suggestionDebounceRef.current = window.setTimeout(() => {
      postSuggestions(transcript);
    }, 2500);

    return () => {
      if (suggestionDebounceRef.current) {
        window.clearTimeout(suggestionDebounceRef.current);
      }
    };
  }, [transcript]);

  // 30-second auto-refresh while recording — keeps suggestions alive during pauses.
  useEffect(() => {
    if (!isRecording) {
      if (autoRefreshRef.current) {
        window.clearInterval(autoRefreshRef.current);
        autoRefreshRef.current = null;
      }
      return;
    }
    autoRefreshRef.current = window.setInterval(() => {
      if (transcriptRef.current.length > 0) {
        postSuggestionsRef.current(transcriptRef.current);
      }
    }, 30000);
    return () => {
      if (autoRefreshRef.current) {
        window.clearInterval(autoRefreshRef.current);
        autoRefreshRef.current = null;
      }
    };
  }, [isRecording]);

  useEffect(() => {
    if (finalTranscript) {
      console.log("FINAL TRANSCRIPT:", finalTranscript);
    }
  }, [finalTranscript]);

  useEffect(() => {
    if (previewText) {
      console.log("PREVIEW:", previewText);
    }
  }, [previewText]);

  const exportAll = async () => {
    const res = await fetch(`${API_URL}/export`);
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `twinmind-export-${toTimestamp().replaceAll(":", "-")}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-950 to-blue-950/40 p-4 text-slate-200">
      <SettingsPanel
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        apiUrl={API_URL}
        onSaved={(cfg) => {
          setApiKeyConfigured(Boolean(cfg.groq_api_key));
          setSettingsOpen(false);
        }}
      />

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-blue-900/30 pb-4">
        <div>
          <h1 className="bg-gradient-to-r from-blue-200 via-white to-orange-200 bg-clip-text text-2xl font-bold tracking-tight text-transparent">
            TwinMind
          </h1>
          <p className="text-xs font-medium uppercase tracking-widest text-blue-300/70">Live suggestions co-pilot</p>
        </div>
        <div className="flex items-center gap-2">
          {!apiKeyConfigured && (
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="rounded-lg border border-orange-500/60 bg-orange-500/15 px-3 py-1.5 text-xs font-semibold text-orange-200 shadow-sm animate-pulse hover:bg-orange-500/25 transition-colors"
            >
              ⚠ Set API Key
            </button>
          )}
          <button
            type="button"
            className="rounded-lg border border-slate-600/60 bg-slate-800/80 px-3 py-2 text-sm font-medium text-slate-300 shadow-sm transition-all duration-200 hover:border-blue-500/50 hover:text-white active:scale-[0.97]"
            onClick={() => setSettingsOpen(true)}
            title="Settings"
          >
            ⚙ Settings
          </button>
          <button
            type="button"
            className="rounded-lg border border-orange-500/50 bg-blue-600/90 px-4 py-2 text-sm font-semibold text-white shadow-md transition-all duration-200 hover:bg-orange-500 hover:shadow-[0_0_20px_rgba(249,115,22,0.35)] active:scale-[0.98]"
            onClick={exportAll}
          >
            Export JSON
          </button>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <TranscriptPanel
          transcript={transcript}
          finalTranscript={finalTranscript}
          previewText={previewText}
          isRecording={isRecording}
          onToggleRecording={onToggleRecording}
        />
        <SuggestionsPanel
          suggestions={displayedSuggestions}
          suggestionHistory={suggestionHistory}
          currentSegmentId={currentSegmentId}
          onSuggestionClick={(s) => sendChat(s.preview, true)}
          onRefresh={() => postSuggestions(transcript)}
          loading={suggestionLoading}
          context={context}
          latestBatchId={latestBatchId}
          newBatchPulse={newBatchPulse}
          topicShiftLabel={topicShiftBanner}
        />
        <ChatPanel chat={chat} onSend={(msg) => sendChat(msg, false)} onReset={() => setChat([])} loading={chatLoading} />
      </div>
    </div>
  );
}
