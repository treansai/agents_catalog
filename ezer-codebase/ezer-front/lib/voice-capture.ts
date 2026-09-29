/**
 * Capture push-to-talk, côté navigateur.
 *
 * L'enregistrement part tel que `MediaRecorder` le produit — webm/opus, ou mp4 sur Safari : Scribe
 * accepte les formats courants, si bien qu'il n'y a plus ni décodage, ni rééchantillonnage, ni
 * réencodage en WAV avant l'envoi. C'est aussi ce qui voyage le plus léger.
 */

export const MAX_RECORDING_MS = 15_000;

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

/** Le nom de fichier renseigne le serveur sur le conteneur, que le navigateur choisit seul. */
export function recordingFilename(audio: Blob): string {
  const type = audio.type.split(";", 1)[0].trim().toLowerCase();
  if (type === "audio/mp4" || type === "audio/aac") return "speech.mp4";
  if (type === "audio/mpeg") return "speech.mp3";
  if (type === "audio/ogg") return "speech.ogg";
  return "speech.webm";
}

export interface VoiceRecording {
  /** Clôt l'enregistrement et rend le son capté. Le micro est relâché dans tous les cas. */
  stop: () => Promise<Blob>;
  /** Abandonne le tour : le micro est relâché et rien n'est envoyé. */
  cancel: () => void;
}

/** Ouvre le micro et commence à enregistrer. Le flux est toujours relâché, y compris en échec. */
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
      // On relâche le micro ici pour que le témoin du navigateur s'éteigne sur tous les chemins,
      // y compris quand le plafond de 15 s tombe alors que l'utilisateur s'est éloigné.
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

  const halt = () => {
    clearTimeout(timeout);
    if (recorder.state === "recording") {
      recorder.stop();
    }
  };

  return {
    stop: async () => {
      halt();
      try {
        const audio = await finished;
        if (audio.size === 0) {
          throw new VoiceCaptureError("Aucun son n’a été enregistré.");
        }
        return audio;
      } catch (error) {
        if (error instanceof VoiceCaptureError) {
          throw error;
        }
        throw new VoiceCaptureError("L’enregistrement n’a pas pu être traité.");
      } finally {
        release();
      }
    },
    cancel: () => {
      halt();
      release();
    },
  };
}
