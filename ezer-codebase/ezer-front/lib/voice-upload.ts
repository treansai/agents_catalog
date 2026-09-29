import "server-only";

/**
 * Lecture bornée d'un enregistrement envoyé par le navigateur.
 *
 * L'audio voyage en multipart plutôt qu'en base64 dans du JSON : ElevenLabs attend un fichier, et
 * un octet transporté reste un octet au lieu d'enfler d'un tiers. Les deux routes vocales partagent
 * cette lecture pour que la même borne s'applique des deux côtés.
 */

/** ~15 s d'opus pèsent quelques dizaines de kilo-octets ; 4 Mio laissent la marge d'un format non compressé. */
export const MAX_AUDIO_BYTES = 4 * 1024 * 1024;

const AUDIO_FIELD = "audio";

export class AudioUploadError extends Error {
  constructor(readonly status: 400 | 413 | 415) {
    super(
      status === 413
        ? "Audio upload too large"
        : status === 415
          ? "Unsupported media type"
          : "Invalid audio upload",
    );
    this.name = "AudioUploadError";
  }
}

export async function readAudioForm(request: Request): Promise<{ audio: Blob; form: FormData }> {
  const contentType = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (contentType !== "multipart/form-data") {
    throw new AudioUploadError(415);
  }

  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    if (!/^\d+$/.test(declaredLength)) {
      throw new AudioUploadError(400);
    }
    if (Number(declaredLength) > MAX_AUDIO_BYTES) {
      throw new AudioUploadError(413);
    }
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    throw new AudioUploadError(400);
  }

  const audio = form.get(AUDIO_FIELD);
  if (!(audio instanceof Blob) || audio.size === 0) {
    throw new AudioUploadError(400);
  }
  // `content-length` manque quand la requête est découpée : la taille réelle est la seule borne sûre.
  if (audio.size > MAX_AUDIO_BYTES) {
    throw new AudioUploadError(413);
  }

  return { audio, form };
}

/** Le nom de fichier renseigne ElevenLabs sur le conteneur, que le navigateur choisit seul. */
export function uploadFilename(audio: Blob): string {
  const type = audio.type.split(";", 1)[0].trim().toLowerCase();
  if (type === "audio/mp4" || type === "audio/aac") return "speech.mp4";
  if (type === "audio/mpeg") return "speech.mp3";
  if (type === "audio/ogg") return "speech.ogg";
  if (type === "audio/wav" || type === "audio/x-wav") return "speech.wav";
  return "speech.webm";
}
