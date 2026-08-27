/**
 * Push-to-talk capture for the dashboard.
 *
 * MediaRecorder emits webm/opus, which the audio model does not accept, so the
 * recording is decoded and re-encoded as 16 kHz mono WAV in the browser. That is
 * the documented input format and it also divides the upload size by about ten.
 */

export const MAX_RECORDING_MS = 15_000;
const TARGET_SAMPLE_RATE = 16_000;

export class VoiceCaptureError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "VoiceCaptureError";
  }
}

export function isVoiceCaptureSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    navigator.mediaDevices?.getUserMedia !== undefined
  );
}

function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeAscii = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (const sample of samples) {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }

  return buffer;
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  // Chunked so a long recording cannot blow the argument limit of String.fromCharCode.
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

async function toWavBase64(blob: Blob): Promise<string> {
  const encoded = await blob.arrayBuffer();
  const decodeContext = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await decodeContext.decodeAudioData(encoded);
  } finally {
    await decodeContext.close();
  }

  const frames = Math.max(
    1,
    Math.round((decoded.duration * TARGET_SAMPLE_RATE) as number),
  );
  const offline = new OfflineAudioContext(1, frames, TARGET_SAMPLE_RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const resampled = await offline.startRendering();

  return toBase64(encodeWav(resampled.getChannelData(0), TARGET_SAMPLE_RATE));
}

export interface VoiceRecording {
  stop: () => Promise<string>;
}

/**
 * Opens the microphone and starts recording. The returned `stop` resolves with the
 * base64 WAV payload. The stream is always released, including on failure.
 */
export async function startRecording(): Promise<VoiceRecording> {
  if (!isVoiceCaptureSupported()) {
    throw new VoiceCaptureError("Votre navigateur ne permet pas la commande vocale.");
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    throw new VoiceCaptureError("Accès au micro refusé.");
  }

  const release = () => {
    for (const track of stream.getTracks()) {
      track.stop();
    }
  };

  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(stream);
  } catch {
    release();
    throw new VoiceCaptureError("Votre navigateur ne permet pas la commande vocale.");
  }

  const chunks: Blob[] = [];
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) {
      chunks.push(event.data);
    }
  });

  const finished = new Promise<Blob>((resolve, reject) => {
    recorder.addEventListener("stop", () => {
      // Release the microphone here so the browser's in-use indicator clears on every
      // path, including the 15 s cap firing while the user has walked away.
      release();
      resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
    });
    recorder.addEventListener("error", () =>
      reject(new VoiceCaptureError("L’enregistrement a échoué.")),
    );
  });

  recorder.start();
  const timeout = setTimeout(() => {
    if (recorder.state === "recording") {
      recorder.stop();
    }
  }, MAX_RECORDING_MS);

  return {
    stop: async () => {
      clearTimeout(timeout);
      if (recorder.state === "recording") {
        recorder.stop();
      }
      try {
        const blob = await finished;
        if (blob.size === 0) {
          throw new VoiceCaptureError("Aucun son n’a été enregistré.");
        }
        return await toWavBase64(blob);
      } catch (error) {
        if (error instanceof VoiceCaptureError) {
          throw error;
        }
        throw new VoiceCaptureError("L’enregistrement n’a pas pu être traité.");
      } finally {
        release();
      }
    },
  };
}
