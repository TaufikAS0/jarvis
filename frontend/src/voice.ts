/**
 * Voice input (Web Speech API) and audio output (AudioContext) for JARVIS.
 */

// ---------------------------------------------------------------------------
// Speech Recognition
// ---------------------------------------------------------------------------

export interface VoiceInput {
  start(): void;
  stop(): void;
  pause(): void;
  resume(): void;
  setLanguage(lang: string): void;
}

export function isNetworkOnline(): boolean {
  return navigator.onLine !== false;
}

// ---------------------------------------------------------------------------
// Local Transcription (via backend Whisper)
// ---------------------------------------------------------------------------

/** Decode any browser audio blob (webm/opus) and re-encode as 16-bit 16kHz WAV.
 *  This removes the ffmpeg dependency on the backend. */
async function convertToWav(blob: Blob, audioCtx: AudioContext): Promise<Blob> {
  const arrayBuffer = await blob.arrayBuffer();
  const decoded = await audioCtx.decodeAudioData(arrayBuffer);

  const targetSampleRate = 16000;
  const offlineCtx = new OfflineAudioContext(1, Math.ceil(decoded.duration * targetSampleRate), targetSampleRate);
  const source = offlineCtx.createBufferSource();
  source.buffer = decoded;
  source.connect(offlineCtx.destination);
  source.start(0);
  const rendered = await offlineCtx.startRendering();

  const pcm = rendered.getChannelData(0);
  const numSamples = pcm.length;
  const wavBuffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(wavBuffer);
  const writeStr = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };

  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, targetSampleRate, true);
  view.setUint32(28, targetSampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, numSamples * 2, true);
  for (let i = 0; i < numSamples; i++) {
    view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
  }
  return new Blob([wavBuffer], { type: 'audio/wav' });
}

export function createLocalVoiceInput(
  onTranscript: (text: string) => void,
  onError: (msg: string) => void,
  _initialLanguage = 'en-US'
): VoiceInput {
  let mediaStream: MediaStream | null = null;
  let mediaRecorder: MediaRecorder | null = null;
  let audioCtx: AudioContext | null = null;
  let audioChunks: Blob[] = [];
  let shouldListen = false;
  let paused = false;
  let isRecording = false;
  let hasSpeech = false;
  let silenceTimer: ReturnType<typeof setTimeout> | null = null;
  let restartTimer: ReturnType<typeof setTimeout> | null = null;
  let maxTimer: ReturnType<typeof setTimeout> | null = null;
  let vadFrameId = 0;

  const MIN_SPEECH_RMS = 2.2;
  const MIN_SPEECH_PEAK = 12;
  const MIN_TRANSCRIBE_RMS = 1.2;
  const MIN_TRANSCRIBE_PEAK = 8;
  const MIN_SPEECH_FRAMES = 3;
  const SILENCE_AFTER_SPEECH = 2500;
  const MAX_RECORD_MS = 15000;
  console.log('[whisper] VAD config: rms_threshold=', MIN_SPEECH_RMS, 'peak_threshold=', MIN_SPEECH_PEAK, 'silence_timeout=', SILENCE_AFTER_SPEECH);

  function clearTimers() {
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
    if (restartTimer) {
      clearTimeout(restartTimer);
      restartTimer = null;
    }
    if (maxTimer) {
      clearTimeout(maxTimer);
      maxTimer = null;
    }
    if (vadFrameId) {
      cancelAnimationFrame(vadFrameId);
      vadFrameId = 0;
    }
  }

  async function cleanupAudioResources() {
    mediaStream?.getTracks().forEach((track) => track.stop());
    mediaStream = null;
    if (audioCtx) {
      await audioCtx.close().catch(() => {});
      audioCtx = null;
    }
  }

  function measureInputLevel(data: Uint8Array): { rms: number; peak: number } {
    let sumSquares = 0;
    let peak = 0;
    for (let i = 0; i < data.length; i++) {
      const centered = (data[i] - 128) / 128;
      const amplitude = Math.abs(centered);
      sumSquares += centered * centered;
      if (amplitude > peak) peak = amplitude;
    }
    return {
      rms: Math.sqrt(sumSquares / data.length) * 100,
      peak: peak * 100,
    };
  }

  async function startRecording() {
    console.log('[whisper] startRecording called - isRecording:', isRecording, 'paused:', paused, 'shouldListen:', shouldListen);
    if (isRecording || paused || !shouldListen) return;
    isRecording = true;

    try {
      const AC = (window as any).AudioContext || (window as any).webkitAudioContext;
      if (!AC) {
        isRecording = false;
        onError('AudioContext not supported');
        return;
      }

      audioCtx = new AC() as AudioContext;
      await audioCtx.resume();
      console.log('[whisper] AudioContext state:', audioCtx.state);

      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const trackSettings = mediaStream.getAudioTracks()[0]?.getSettings?.();
      console.log('[whisper] Microphone ready:', trackSettings || 'ok');

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : '';

      mediaRecorder = mimeType
        ? new MediaRecorder(mediaStream, { mimeType })
        : new MediaRecorder(mediaStream);

      audioChunks = [];
      hasSpeech = false;
      let frameCount = 0;
      let speechFrameCount = 0;
      let noiseFloor = 0;
      let peakRmsObserved = 0;
      let peakLevelObserved = 0;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        isRecording = false;
        clearTimers();
        const usableFallback =
          !hasSpeech && audioChunks.length > 0 && (peakRmsObserved >= MIN_TRANSCRIBE_RMS || peakLevelObserved >= MIN_TRANSCRIBE_PEAK);

        if ((hasSpeech || usableFallback) && audioChunks.length > 0) {
          const webmBlob = new Blob(audioChunks, { type: mimeType || 'audio/webm' });
          audioChunks = [];
          try {
            console.log('[whisper] Transcribing captured audio. hasSpeech=', hasSpeech, 'fallback=', usableFallback, 'bytes=', webmBlob.size, 'peakRms=', peakRmsObserved.toFixed(2), 'peak=', peakLevelObserved.toFixed(2));
            const wavBlob = await convertToWav(webmBlob, audioCtx as AudioContext);
            await cleanupAudioResources();
            await transcribeAudio(wavBlob, 'audio/wav');
          } catch (error) {
            console.error('[whisper] Conversion error:', error);
            await cleanupAudioResources();
          }
        } else {
          const discardedChunks = audioChunks.length;
          audioChunks = [];
          console.warn('[whisper] Recorder stopped without usable speech. chunks=', discardedChunks, 'peakRms=', peakRmsObserved.toFixed(2), 'peak=', peakLevelObserved.toFixed(2));
          await cleanupAudioResources();
        }

        if (shouldListen && !paused) {
          restartTimer = setTimeout(() => startRecording(), 150);
        }
      };

      mediaRecorder.start(100);
      console.log('[whisper] MediaRecorder started, mimeType:', mediaRecorder.mimeType);

      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.15;
      const mic = audioCtx.createMediaStreamSource(mediaStream);
      mic.connect(analyser);
      const data = new Uint8Array(analyser.fftSize);

      maxTimer = setTimeout(() => {
        if (isRecording) {
          console.warn('[whisper] Max record timeout reached. Stopping recorder.');
          mediaRecorder?.stop();
        }
      }, MAX_RECORD_MS);

      function vad() {
        if (!isRecording) return;

        analyser.getByteTimeDomainData(data);
        const { rms, peak } = measureInputLevel(data);
        frameCount += 1;

        if (frameCount === 1) {
          noiseFloor = rms;
        } else {
          noiseFloor = noiseFloor * 0.92 + rms * 0.08;
        }

        peakRmsObserved = Math.max(peakRmsObserved, rms);
        peakLevelObserved = Math.max(peakLevelObserved, peak);

        const dynamicRmsThreshold = Math.max(MIN_SPEECH_RMS, noiseFloor * 2.8);
        const speechDetected = rms >= dynamicRmsThreshold || peak >= Math.max(MIN_SPEECH_PEAK, dynamicRmsThreshold * 3.2);

        if (speechDetected) {
          speechFrameCount += 1;
        } else {
          speechFrameCount = Math.max(0, speechFrameCount - 1);
        }

        if (frameCount % 30 === 0) {
          console.log(
            '[whisper] VAD rms=', rms.toFixed(2),
            'peak=', peak.toFixed(2),
            'noise=', noiseFloor.toFixed(2),
            'threshold=', dynamicRmsThreshold.toFixed(2),
            'speechFrames=', speechFrameCount,
            'chunks=', audioChunks.length
          );
        }

        if (speechFrameCount >= MIN_SPEECH_FRAMES) {
          hasSpeech = true;
          if (silenceTimer) {
            clearTimeout(silenceTimer);
            silenceTimer = null;
          }
        } else if (hasSpeech && !silenceTimer) {
          silenceTimer = setTimeout(() => {
            if (isRecording) mediaRecorder?.stop();
          }, SILENCE_AFTER_SPEECH);
        }

        vadFrameId = requestAnimationFrame(vad);
      }

      vad();
    } catch (err: unknown) {
      clearTimers();
      isRecording = false;
      await cleanupAudioResources();
      const error = err as { name?: string; message?: string };
      if (error.name === 'NotAllowedError') {
        onError('Akses mikrofon ditolak. Izinkan mikrofon di browser.');
        shouldListen = false;
      } else {
        onError('Mikrofon error: ' + (error.message ?? err));
      }
    }
  }

  async function transcribeAudio(blob: Blob, mimeType: string) {
    try {
      console.log('[whisper] Uploading audio for transcription. bytes=', blob.size, 'mimeType=', mimeType);
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      const response = await fetch('/api/transcribe', {
        method: 'POST',
        headers: { 'Content-Type': mimeType },
        body: blob,
        signal: controller.signal,
      });
      clearTimeout(timeout);
      console.log('[whisper] /api/transcribe response status=', response.status);
      if (!response.ok) {
        const err = await response.json().catch(() => ({ error: response.statusText }));
        onError('Transcribe gagal: ' + err.error);
        return;
      }
      const result = await response.json();
      console.log('[whisper] Transcript result:', result);
      if (result.text?.trim()) {
        onTranscript(result.text.trim());
      } else {
        console.warn('[whisper] Transcript came back empty.');
        onError('Suara tertangkap, tetapi transkripsi masih kosong. Coba bicara lebih dekat atau lebih jelas.');
      }
    } catch (err: unknown) {
      const error = err as { name?: string; message?: string };
      if (error.name === 'AbortError') {
        onError('Whisper timeout - coba lagi.');
      } else {
        onError('Transcribe error: ' + error.message);
      }
    }
  }

  return {
    start() {
      shouldListen = true;
      paused = false;
      startRecording();
    },
    stop() {
      shouldListen = false;
      paused = false;
      clearTimers();
      if (mediaRecorder && isRecording) mediaRecorder.stop();
      void cleanupAudioResources();
    },
    pause() {
      paused = true;
      clearTimers();
      if (mediaRecorder && isRecording) mediaRecorder.stop();
    },
    resume() {
      paused = false;
      if (shouldListen) startRecording();
    },
    setLanguage() {},
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const webkitSpeechRecognition: any;

function normalizeSpeechRecognitionLanguage(lang?: string): string {
  const value = (lang || 'en-US').trim().toLowerCase();
  return value.startsWith('id') ? 'id-ID' : 'en-US';
}

export function createVoiceInput(
  onTranscript: (text: string) => void,
  onError: (msg: string) => void,
  initialLanguage = 'en-US'
): VoiceInput {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const SR = (window as any).SpeechRecognition || (typeof webkitSpeechRecognition !== 'undefined' ? webkitSpeechRecognition : null);
  if (!SR) {
    onError('Speech recognition not supported in this browser');
    return { start() {}, stop() {}, pause() {}, resume() {}, setLanguage() {} };
  }

  const recognition = new SR();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = normalizeSpeechRecognitionLanguage(initialLanguage);

  let shouldListen = false;
  let paused = false;

  recognition.onresult = (event: any) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        const text = event.results[i][0].transcript.trim();
        if (text) onTranscript(text);
      }
    }
  };

  recognition.onend = () => {
    if (shouldListen && !paused) {
      try {
        recognition.start();
      } catch {
      }
    }
  };

  recognition.onerror = (event: any) => {
    if (!shouldListen) {
      return;
    }
    if (event.error === 'not-allowed') {
      onError('Microphone access denied. Please allow microphone access.');
      shouldListen = false;
    } else if (event.error === 'no-speech') {
    } else if (event.error === 'aborted') {
    } else if (event.error === 'network-error') {
      onError('Network error - Internet required for speech recognition. Use text input instead.');
      shouldListen = false;
    } else if (event.error === 'service-unavailable') {
      onError('Speech service unavailable (offline?). Use text input instead.');
      shouldListen = false;
    } else {
      console.warn('[voice] recognition error:', event.error);
      onError('Speech recognition error: ' + event.error + '. Use text input instead.');
    }
  };

  let recognitionTimeout: ReturnType<typeof setTimeout> | null = null;
  const originalStart = recognition.start.bind(recognition);
  recognition.start = function() {
    originalStart();
    recognitionTimeout = setTimeout(() => {
      if (shouldListen && !paused) {
        recognition.abort();
        onError('Speech recognition timeout - no internet connection detected. Use text input instead.');
      }
    }, 15000);
  };

  const originalStop = recognition.stop.bind(recognition);
  recognition.stop = function() {
    if (recognitionTimeout) clearTimeout(recognitionTimeout);
    originalStop();
  };

  return {
    start() {
      shouldListen = true;
      paused = false;
      try {
        recognition.start();
      } catch {
      }
    },
    stop() {
      shouldListen = false;
      paused = false;
      recognition.stop();
    },
    pause() {
      paused = true;
      recognition.stop();
    },
    resume() {
      paused = false;
      if (shouldListen) {
        try {
          recognition.start();
        } catch {
        }
      }
    },
    setLanguage(lang: string) {
      recognition.lang = normalizeSpeechRecognitionLanguage(lang);
    },
  };
}

// ---------------------------------------------------------------------------
// Audio Player
// ---------------------------------------------------------------------------

export interface AudioPlayer {
  enqueue(base64: string): Promise<void>;
  stop(): void;
  getAnalyser(): AnalyserNode;
  onFinished(cb: () => void): void;
}

export function createAudioPlayer(): AudioPlayer {
  const audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.8;
  analyser.connect(audioCtx.destination);

  const queue: AudioBuffer[] = [];
  let isPlaying = false;
  let currentSource: AudioBufferSourceNode | null = null;
  let finishedCallback: (() => void) | null = null;

  function playNext() {
    if (queue.length === 0) {
      isPlaying = false;
      currentSource = null;
      finishedCallback?.();
      return;
    }

    isPlaying = true;
    const buffer = queue.shift()!;
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);
    currentSource = source;

    source.onended = () => {
      if (currentSource === source) {
        playNext();
      }
    };

    source.start();
  }

  return {
    async enqueue(base64: string) {
      if (audioCtx.state === 'suspended') {
        await audioCtx.resume();
      }

      try {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i);
        }
        const audioBuffer = await audioCtx.decodeAudioData(bytes.buffer.slice(0));
        queue.push(audioBuffer);
        if (!isPlaying) playNext();
      } catch (err) {
        console.error('[audio] decode error:', err);
        if (!isPlaying && queue.length > 0) playNext();
      }
    },

    stop() {
      queue.length = 0;
      if (currentSource) {
        try {
          currentSource.stop();
        } catch {
        }
        currentSource = null;
      }
      isPlaying = false;
    },

    getAnalyser() {
      return analyser;
    },

    onFinished(cb: () => void) {
      finishedCallback = cb;
    },
  };
}


