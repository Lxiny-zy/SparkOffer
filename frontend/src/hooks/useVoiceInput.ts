import { useState, useRef, useCallback, useEffect } from "react";
import { transcribeAudio } from "../api/interview";

interface UseVoiceInputOptions {
  onResult?: (text: string) => void;
  preferServer?: boolean;
}

interface UseVoiceInputReturn {
  isListening: boolean;
  isTranscribing: boolean;
  isSupported: boolean;
  backend: string;
  toggle: () => void;
}

/**
 * Voice input hook with two backends:
 * 1. Web Speech API (browser-native, free, no config needed)
 * 2. DashScope ASR (server-side, requires API key + Qiniu OSS)
 *
 * Automatically uses Web Speech API if available; falls back to
 * server-side transcription when Web Speech API is not supported.
 */
export default function useVoiceInput({ onResult, preferServer = false }: UseVoiceInputOptions = {}): UseVoiceInputReturn {
  const [isListening, setIsListening] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [backend, setBackend] = useState<string>("none"); // "webspeech" | "server" | "none"
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<any>(null);
  const onResultRef = useRef(onResult);

  useEffect(() => { onResultRef.current = onResult; }, [onResult]);

  // Detect available backends
  const hasWebSpeech = typeof window !== "undefined"
    && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);
  const hasMediaRecorder = typeof navigator !== "undefined"
    && !!navigator.mediaDevices?.getUserMedia;

  const isSupported = hasWebSpeech || hasMediaRecorder;

  // Determine which backend to use
  useEffect(() => {
    if (preferServer && hasMediaRecorder) {
      setBackend("server");
    } else if (hasWebSpeech) {
      setBackend("webspeech");
    } else if (hasMediaRecorder) {
      setBackend("server");
    } else {
      setBackend("none");
    }
  }, [preferServer, hasWebSpeech, hasMediaRecorder]);

  // ── Web Speech API ──
  const startWebSpeech = useCallback(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = false;

    let fullText = "";

    recognition.onresult = (event: any) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          fullText += event.results[i][0].transcript;
        }
      }
    };

    recognition.onerror = (event: any) => {
      console.error("Web Speech error:", event.error);
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
      if (fullText && onResultRef.current) {
        onResultRef.current(fullText);
      }
    };

    recognitionRef.current = recognition;
    recognition.start();
    setIsListening(true);
  }, []);

  const stopWebSpeech = useCallback(() => {
    setIsListening(false);
    recognitionRef.current?.stop();
    recognitionRef.current = null;
  }, []);

  // ── Server-side ASR (DashScope) ──
  const startServer = useCallback(async () => {
    if (!hasMediaRecorder) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";

      const recorder = new MediaRecorder(stream, { mimeType });
      chunksRef.current = [];

      recorder.ondataavailable = (e: BlobEvent) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      mediaRecorderRef.current = recorder;
      recorder.start(250);
      setIsListening(true);
    } catch (err) {
      console.error("Microphone access failed:", err);
      setIsListening(false);
    }
  }, [hasMediaRecorder]);

  const stopServer = useCallback(async () => {
    setIsListening(false);
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;

    return new Promise<void>((resolve) => {
      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
        chunksRef.current = [];

        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        mediaRecorderRef.current = null;

        if (blob.size < 1000) {
          resolve();
          return;
        }

        setIsTranscribing(true);
        try {
          const { text } = await transcribeAudio(blob);
          if (text && onResultRef.current) {
            onResultRef.current(text);
          }
        } catch (err) {
          console.error("Transcription failed:", err);
          // If server transcription fails, inform user
          if (onResultRef.current) {
            onResultRef.current("");
          }
        } finally {
          setIsTranscribing(false);
        }
        resolve();
      };
      recorder.stop();
    });
  }, []);

  // ── Unified toggle ──
  const toggle = useCallback(() => {
    if (isListening) {
      if (backend === "webspeech") stopWebSpeech();
      else stopServer();
    } else if (!isTranscribing) {
      if (backend === "webspeech") startWebSpeech();
      else startServer();
    }
  }, [isListening, isTranscribing, backend, startWebSpeech, stopWebSpeech, startServer, stopServer]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      if (mediaRecorderRef.current?.state !== "inactive") {
        try { mediaRecorderRef.current?.stop(); } catch { /* cleanup */ }
      }
    };
  }, []);

  return { isListening, isTranscribing, isSupported, backend, toggle };
}
