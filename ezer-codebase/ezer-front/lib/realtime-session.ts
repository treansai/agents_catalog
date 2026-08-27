/**
 * Session vocale temps réel, côté navigateur.
 *
 * Le micro et le haut-parleur sont reliés directement au modèle temps réel par WebRTC : il n'y a
 * plus d'aller-retour transcription puis synthèse. Le navigateur n'obtient qu'un secret éphémère
 * délivré par notre serveur, et l'unique outil de la session est déclaré côté serveur.
 */

const CALLS_URL = "https://api.openai.com/v1/realtime/calls";
const DATA_CHANNEL = "oai-events";
const SESSION_TIMEOUT_MS = 20_000;

export type RealtimeSpeaker = "idle" | "user" | "assistant";

export class RealtimeSessionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RealtimeSessionError";
  }
}

export interface RealtimeHandlers {
  onSpeaker: (speaker: RealtimeSpeaker) => void;
  onUserTranscript: (text: string) => void;
  onAssistantTranscript: (text: string) => void;
  /** Exécute l'outil et renvoie le texte que le modèle lira. */
  onToolCall: (name: string, args: Record<string, unknown>) => Promise<string>;
  onError: (message: string) => void;
  onClosed: () => void;
}

export interface RealtimeConnection {
  close: () => void;
}

interface FunctionCall {
  name: string;
  callId: string;
  arguments: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function functionCallsIn(event: Record<string, unknown>): FunctionCall[] {
  const response = event.response;
  if (!isRecord(response) || !Array.isArray(response.output)) return [];
  const calls: FunctionCall[] = [];
  for (const item of response.output) {
    if (!isRecord(item) || item.type !== "function_call") continue;
    const { name, call_id: callId, arguments: args } = item;
    if (typeof name === "string" && typeof callId === "string") {
      calls.push({ name, callId, arguments: typeof args === "string" ? args : "{}" });
    }
  }
  return calls;
}

function transcriptIn(event: Record<string, unknown>): string | null {
  const transcript = event.transcript;
  return typeof transcript === "string" && transcript.trim() !== "" ? transcript.trim() : null;
}

export function isRealtimeSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof RTCPeerConnection !== "undefined" &&
    navigator.mediaDevices?.getUserMedia !== undefined
  );
}

export async function connectRealtime(handlers: RealtimeHandlers): Promise<RealtimeConnection> {
  if (!isRealtimeSupported()) {
    throw new RealtimeSessionError("Ce navigateur ne permet pas la conversation vocale.");
  }

  let secretResponse: Response;
  try {
    secretResponse = await fetch("/api/ezer/realtime", { method: "POST" });
  } catch {
    throw new RealtimeSessionError("Ezer est injoignable. Vérifiez que le service tourne.");
  }
  if (!secretResponse.ok) {
    const payload: unknown = await secretResponse.json().catch(() => null);
    const message =
      isRecord(payload) && typeof payload.message === "string"
        ? payload.message
        : "La session vocale n’a pas pu être ouverte.";
    throw new RealtimeSessionError(message);
  }
  const session = (await secretResponse.json()) as { client_secret: string };

  let microphone: MediaStream;
  try {
    microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    throw new RealtimeSessionError("Le micro n’a pas pu être ouvert.");
  }

  const connection = new RTCPeerConnection();
  const speaker = new Audio();
  speaker.autoplay = true;
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    for (const track of microphone.getTracks()) track.stop();
    speaker.srcObject = null;
    connection.close();
    handlers.onClosed();
  };

  connection.ontrack = (event) => {
    const [stream] = event.streams;
    if (stream !== undefined) speaker.srcObject = stream;
  };
  connection.onconnectionstatechange = () => {
    if (["failed", "disconnected", "closed"].includes(connection.connectionState)) close();
  };

  for (const track of microphone.getAudioTracks()) connection.addTrack(track, microphone);

  const channel = connection.createDataChannel(DATA_CHANNEL);
  const send = (payload: unknown) => {
    if (channel.readyState === "open") channel.send(JSON.stringify(payload));
  };

  channel.addEventListener("message", (message: MessageEvent<string>) => {
    let event: unknown;
    try {
      event = JSON.parse(message.data);
    } catch {
      return;
    }
    if (!isRecord(event) || typeof event.type !== "string") return;
    const type = event.type;

    if (type === "error") {
      handlers.onError("La session vocale a rencontré une erreur.");
      return;
    }
    if (type === "input_audio_buffer.speech_started") {
      handlers.onSpeaker("user");
      return;
    }
    if (type === "conversation.item.input_audio_transcription.completed") {
      const transcript = transcriptIn(event);
      if (transcript !== null) handlers.onUserTranscript(transcript);
      return;
    }
    if (type === "response.created" || type.startsWith("response.output_audio")) {
      handlers.onSpeaker("assistant");
    }
    if (type.endsWith("audio_transcript.done")) {
      const transcript = transcriptIn(event);
      if (transcript !== null) handlers.onAssistantTranscript(transcript);
      return;
    }
    if (type !== "response.done") return;

    const calls = functionCallsIn(event);
    if (calls.length === 0) {
      handlers.onSpeaker("idle");
      return;
    }

    void (async () => {
      for (const call of calls) {
        let output: string;
        try {
          const parsed: unknown = JSON.parse(call.arguments);
          output = await handlers.onToolCall(call.name, isRecord(parsed) ? parsed : {});
        } catch {
          output = "L'outil a échoué. Dis-le simplement à l'utilisateur.";
        }
        send({
          type: "conversation.item.create",
          item: { type: "function_call_output", call_id: call.callId, output },
        });
      }
      // Le modèle reprend la parole avec le résultat de l'outil.
      send({ type: "response.create" });
    })();
  });

  const offer = await connection.createOffer();
  await connection.setLocalDescription(offer);

  let answer: Response;
  try {
    answer = await fetch(CALLS_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.client_secret}`,
        "Content-Type": "application/sdp",
      },
      body: offer.sdp ?? "",
      signal: AbortSignal.timeout(SESSION_TIMEOUT_MS),
    });
  } catch {
    close();
    throw new RealtimeSessionError("Le service vocal est injoignable.");
  }
  if (!answer.ok) {
    close();
    throw new RealtimeSessionError("La session vocale a été refusée.");
  }

  await connection.setRemoteDescription({ type: "answer", sdp: await answer.text() });
  return { close };
}
