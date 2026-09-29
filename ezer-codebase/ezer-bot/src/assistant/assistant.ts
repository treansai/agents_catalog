import {
  AgentUiError,
  MAX_UI_MESSAGES,
  PROTOCOL_VERSION,
  type AgentUiClient,
  type CatalogComponent,
  type UiActionEvent,
  type UiInstanceRef,
  type UiMessage,
  UiInstanceLedger,
  catalogPromptPayload,
  newMessageId,
  stripSessionKeys
} from "../agent-ui/agent-ui";
import type {
  MailboxClient,
  MailboxStats,
  MessageBody,
  MessageHeader,
  SenderTally,
  TriagedAnalysis
} from "../mailbox/mailbox";
import { MailboxUnavailableError } from "../mailbox/mailbox";
import { UNTRUSTED_EMAIL_POLICY } from "../security";
import type { AnthropicModelConfig, ChatModel, ChatModelFactory, ChatTurn, ToolCall } from "./chat-model";
import { createAnthropicChatModel } from "./chat-model";
import { ANALYST_SYSTEM_PROMPT, ASSISTANT_PROMPT_VERSION, ORCHESTRATOR_SYSTEM_PROMPT, SUMMARIZER_SYSTEM_PROMPT } from "./prompts";
import { ALL_TOOL_SCHEMAS } from "./tools";

export { ALL_TOOL_SCHEMAS, ANALYST_SYSTEM_PROMPT, ASSISTANT_PROMPT_VERSION, SUMMARIZER_SYSTEM_PROMPT };

const MAX_TURNS = 8;
const MAX_REPLY_CHARS = 8_000;
const DEFAULT_TOP = 10;

export interface AssistantTurn {
  role: "user" | "assistant";
  content: string;
}

export interface MessageHeaderView {
  message_id: string;
  subject: string;
  sender_name: string;
  sender_address: string;
  received_at: string;
  is_read: boolean;
  has_attachments: boolean;
  snippet: string;
}

export type AssistantView =
  | { kind: "message"; message: MessageHeaderView; body_text: string }
  | { kind: "messages"; title: string; items: MessageHeaderView[] }
  | {
      kind: "senders";
      items: { sender_name: string; sender_address: string; total: number; unread: number }[];
    }
  | {
      kind: "stats";
      mailbox: string;
      total_messages: number;
      unread_messages: number;
      folders: { name: string; total: number; unread: number }[];
    }
  | {
      kind: "triage";
      total: number;
      items: {
        summary: string;
        category: string;
        priority: string;
        needs_human_review: boolean;
        created_at: string;
      }[];
    };

export interface PendingDeletion {
  message_id: string;
  subject: string;
  sender_address: string;
  received_at: string;
}

export interface AssistantAnswer {
  reply: string;
  pending_deletions: PendingDeletion[];
  deleted: PendingDeletion[];
  views: AssistantView[];
  ui_messages: UiMessage[];
  tools_used: string[];
  model_id: string;
  prompt_version: string;
}

function headerLine(header: MessageHeader): string {
  const flags = [header.is_read ? "lu" : "non lu"];
  if (header.has_attachments) flags.push("pièce jointe");
  return [
    `id: ${header.message_id}`,
    `de: ${header.sender_name} <${header.sender_address}>`,
    `objet: ${header.subject}`,
    `reçu: ${header.received_at}`,
    `état: ${flags.join(", ")}`,
    `extrait: ${header.snippet}`
  ].join("\n");
}

function headerView(header: MessageHeader): MessageHeaderView {
  return {
    message_id: header.message_id,
    subject: header.subject,
    sender_name: header.sender_name,
    sender_address: header.sender_address,
    received_at: header.received_at,
    is_read: header.is_read,
    has_attachments: header.has_attachments,
    snippet: header.snippet
  };
}

function messageView(message: MessageBody): AssistantView {
  return { kind: "message", message: headerView(message.header), body_text: message.body_text };
}

function statsView(stats: MailboxStats): AssistantView {
  return {
    kind: "stats",
    mailbox: stats.mailbox,
    total_messages: stats.total_messages,
    unread_messages: stats.unread_messages,
    folders: stats.folders.map(([name, total, unread]) => ({ name, total, unread }))
  };
}

function triageView(analyses: TriagedAnalysis[], total: number): AssistantView {
  return {
    kind: "triage",
    total,
    items: analyses.map((item) => ({
      summary: item.summary,
      category: item.category,
      priority: item.priority,
      needs_human_review: item.needs_human_review,
      created_at: item.created_at
    }))
  };
}

function senderLine(tally: SenderTally): string {
  return `${tally.sender_name} <${tally.sender_address}> — ${tally.total} message(s), ${tally.unread} non lu(s)`;
}

function optionalStr(arguments_: Record<string, unknown>, field: string): string | undefined {
  const value = arguments_[field];
  return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function filterTitle(arguments_: Record<string, unknown>): string {
  const parts: string[] = [];
  if (arguments_.unread_only === true) parts.push("non lus");
  const sender = optionalStr(arguments_, "from_address");
  if (sender !== undefined) parts.push(`de ${sender}`);
  const since = optionalStr(arguments_, "since");
  if (since !== undefined) parts.push(`depuis ${since}`);
  const until = optionalStr(arguments_, "until");
  if (until !== undefined) parts.push(`jusqu'au ${until}`);
  return `Messages ${parts.length > 0 ? parts.join(" · ") : "récents"}`;
}

function asUntrusted(label: string, content: string): string {
  return [UNTRUSTED_EMAIL_POLICY, `--- BEGIN UNTRUSTED MAILBOX DATA (${label}) ---`, content, "--- END UNTRUSTED MAILBOX DATA ---"].join(
    "\n"
  );
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function topOf(arguments_: Record<string, unknown>): number {
  const value = arguments_.top;
  if (typeof value !== "number" || !Number.isInteger(value)) return DEFAULT_TOP;
  return Math.max(1, Math.min(value, 25));
}

function nowIso(): string {
  return new Date().toISOString();
}

function dataSource(value: unknown): Record<string, unknown> | undefined {
  const record = asRecord(value);
  if (record === undefined) return undefined;
  const mode = record.mode;
  if (mode === "inline") return { mode: "inline", value: record.value };
  if (mode === "resolver") {
    const resolverId = record.resolver_id ?? record.resolverId;
    if (typeof resolverId !== "string") return undefined;
    const payload = record.input;
    return {
      mode: "resolver",
      resolverId,
      input: asRecord(payload) !== undefined ? stripSessionKeys(payload as Record<string, unknown>) : {}
    };
  }
  return undefined;
}

const UI_FAILURE_GUIDANCE: Record<string, string> = {
  unknown_component:
    "Ce composant n'existe pas. Consulte get_ui_component_catalog et choisis un identifiant réel ; n'en invente pas un autre.",
  invalid_props:
    "Props refusées par le schéma. Corrige-les une seule fois d'après le schéma du catalogue, sinon réponds en texte.",
  unknown_resolver:
    "Résolveur non autorisé pour ce composant. Utilise un résolveur listé, ou explique que la donnée n'est pas accessible.",
  permission_denied:
    "Accès refusé pour cette session. Informe l'opérateur sans détail technique et n'essaie pas un autre outil pour contourner.",
  payload_too_large: "Charge trop volumineuse. Utilise un résolveur paginé plutôt que des données inline.",
  component_version_mismatch: "Version obsolète. Relis le catalogue et reconstruis le payload avec la version courante."
};

function uiFailure(error: AgentUiError): string {
  const guidance =
    UI_FAILURE_GUIDANCE[error.code] ??
    "Affichage impossible : donne une réponse textuelle utile, sans réessayer.";
  return `Affichage refusé (code ${error.code}). ${guidance}`;
}

function conversation(history: AssistantTurn[]): ChatTurn[] {
  const messages: ChatTurn[] = [];
  let role = "";
  for (const turn of history) {
    if (turn.role === role && messages.length > 0) {
      const previous = messages[messages.length - 1];
      if (previous !== undefined) {
        previous.content = `${previous.content}\n${turn.content}`;
      }
      continue;
    }
    role = turn.role;
    messages.push({ role: turn.role, content: turn.content });
  }
  return messages;
}

class TurnState {
  pending: PendingDeletion[] = [];
  deleted: PendingDeletion[] = [];
  toolsUsed: string[] = [];
  views: AssistantView[] = [];
  seen = new Map<string, MessageHeader>();
  uiMessages: UiMessage[] = [];
  ledger = new UiInstanceLedger();
  catalog = new Map<string, CatalogComponent>();

  constructor(
    readonly accountId: string,
    readonly approvedDeletions: ReadonlySet<string>
  ) {}

  emit(message: UiMessage): boolean {
    if (this.uiMessages.length >= MAX_UI_MESSAGES) return false;
    this.uiMessages.push(message);
    return true;
  }

  show(view: AssistantView): void {
    this.views = this.views.filter((existing) => existing.kind !== view.kind);
    this.views.push(view);
  }

  remember(headers: MessageHeader[]): void {
    for (const header of headers) this.seen.set(header.message_id, header);
  }

  target(messageId: string): PendingDeletion {
    const header = this.seen.get(messageId);
    return {
      message_id: messageId,
      subject: header?.subject ?? "",
      sender_address: header?.sender_address ?? "",
      received_at: header?.received_at ?? ""
    };
  }
}

export class MailboxAssistant {
  private readonly orchestrator: ChatModel;
  private readonly subAgent: ChatModel;
  private readonly modelId: string;

  constructor(
    private readonly config: AnthropicModelConfig,
    private readonly mailbox: MailboxClient,
    modelFactory: ChatModelFactory = createAnthropicChatModel,
    private readonly ui?: AgentUiClient
  ) {
    this.orchestrator = modelFactory(config, { tools: true });
    this.subAgent = modelFactory(config, { tools: false });
    this.modelId = config.anthropicModel;
  }

  async ask(
    accountId: string,
    history: AssistantTurn[],
    approvedDeletions: string[] = [],
    uiAction?: UiActionEvent,
    uiInstances: UiInstanceRef[] = []
  ): Promise<AssistantAnswer> {
    const state = new TurnState(accountId, new Set(approvedDeletions));
    for (const instance of uiInstances.slice(0, 24)) {
      state.ledger.declare(instance.instance_id, instance.component_id, instance.component_version);
    }
    const messages: ChatTurn[] = [{ role: "system", content: ORCHESTRATOR_SYSTEM_PROMPT }, ...conversation(history)];
    if (uiAction !== undefined) {
      const framed = await this.frameAction(uiAction, state);
      if (framed === undefined) {
        return {
          reply: "Cette action n'est pas autorisée pour ce composant.",
          pending_deletions: [],
          deleted: [],
          views: [],
          ui_messages: [],
          tools_used: [],
          model_id: this.modelId,
          prompt_version: ASSISTANT_PROMPT_VERSION
        };
      }
      messages.push({ role: "user", content: framed });
    }

    let reply = "";
    for (let turn = 0; turn < MAX_TURNS; turn += 1) {
      const response = await this.orchestrator.invoke(messages);
      messages.push(response);
      const toolCalls = response.toolCalls ?? [];
      if (toolCalls.length === 0) {
        reply = response.content;
        break;
      }
      for (const call of toolCalls.slice(0, 8)) {
        messages.push({
          role: "tool",
          content: await this.runTool(call, state),
          toolCallId: call.id
        });
      }
    }

    return {
      reply: reply.slice(0, MAX_REPLY_CHARS),
      pending_deletions: state.pending,
      deleted: state.deleted,
      views: state.views.slice(-4),
      ui_messages: state.uiMessages,
      tools_used: state.toolsUsed,
      model_id: this.modelId,
      prompt_version: ASSISTANT_PROMPT_VERSION
    };
  }

  private async runTool(call: ToolCall, state: TurnState): Promise<string> {
    const handler = this.handlers()[call.name];
    if (handler === undefined) return "Outil inconnu.";
    state.toolsUsed.push(call.name);
    try {
      return await handler(call.args, state);
    } catch (error) {
      if (error instanceof MailboxUnavailableError) {
        return `L'outil a échoué (code ${error.code}).`;
      }
      throw error;
    }
  }

  private handlers(): Record<string, (arguments_: Record<string, unknown>, state: TurnState) => Promise<string>> {
    return {
      list_recent_messages: (arguments_, state) => this.listRecent(arguments_, state),
      list_messages: (arguments_, state) => this.listMessages(arguments_, state),
      list_senders: (arguments_, state) => this.listSenders(arguments_, state),
      list_triaged: (arguments_, state) => this.listTriaged(arguments_, state),
      search_messages: (arguments_, state) => this.search(arguments_, state),
      read_message: (arguments_, state) => this.read(arguments_, state),
      summarize_mailbox: (arguments_, state) => this.summarize(arguments_, state),
      analyze_message: (arguments_, state) => this.analyze(arguments_, state),
      request_delete_message: (arguments_, state) => this.requestDelete(arguments_, state),
      get_ui_component_catalog: (arguments_, state) => this.uiCatalog(arguments_, state),
      render_ui_component: (arguments_, state) => this.uiRender(arguments_, state),
      update_ui_component: (arguments_, state) => this.uiUpdate(arguments_, state),
      remove_ui_component: (arguments_, state) => this.uiRemove(arguments_, state)
    };
  }

  private async listRecent(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const headers = await this.mailbox.listRecent(state.accountId, topOf(arguments_));
    state.remember(headers);
    state.show({ kind: "messages", title: "Messages récents", items: headers.map(headerView) });
    if (headers.length === 0) return "Aucun message dans la boîte de réception.";
    return asUntrusted("en-têtes", headers.map(headerLine).join("\n---\n"));
  }

  private async listMessages(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const order = arguments_.order;
    const headers = await this.mailbox.listMessages(state.accountId, {
      top: topOf(arguments_),
      unreadOnly: arguments_.unread_only === true,
      fromAddress: optionalStr(arguments_, "from_address"),
      since: optionalStr(arguments_, "since"),
      until: optionalStr(arguments_, "until"),
      order: order === "asc" ? "asc" : "desc"
    });
    state.remember(headers);
    state.show({ kind: "messages", title: filterTitle(arguments_), items: headers.map(headerView) });
    if (headers.length === 0) return "Aucun message ne correspond à ces critères.";
    return asUntrusted("en-têtes", headers.map(headerLine).join("\n---\n"));
  }

  private async listSenders(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const sample = arguments_.sample;
    const tallies = await this.mailbox.senders(
      state.accountId,
      typeof sample === "number" && Number.isInteger(sample) ? sample : 25
    );
    state.show({
      kind: "senders",
      items: tallies.map((tally) => ({
        sender_name: tally.sender_name,
        sender_address: tally.sender_address,
        total: tally.total,
        unread: tally.unread
      }))
    });
    if (tallies.length === 0) return "Aucun expéditeur récent.";
    return asUntrusted("expéditeurs", tallies.map(senderLine).join("\n"));
  }

  private async listTriaged(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const limit = arguments_.limit;
    const needsReview = arguments_.needs_human_review;
    const { analyses, total } = await this.mailbox.analyses(state.accountId, {
      limit: typeof limit === "number" && Number.isInteger(limit) ? limit : 20,
      category: optionalStr(arguments_, "category"),
      priority: optionalStr(arguments_, "priority"),
      needsHumanReview: typeof needsReview === "boolean" ? needsReview : undefined
    });
    state.show(triageView(analyses, total));
    if (analyses.length === 0) return "Aucune analyse ne correspond à ces critères.";
    const lines = analyses.map((item) => {
      const review = item.needs_human_review ? ", à relire" : "";
      return `[${item.priority}/${item.category}${review}] ${item.summary}`;
    });
    return asUntrusted("analyses", `${total} au total.\n${lines.join("\n")}`);
  }

  private async search(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const query = arguments_.query;
    if (typeof query !== "string") return "Terme de recherche manquant.";
    const headers = await this.mailbox.search(state.accountId, query, topOf(arguments_));
    state.remember(headers);
    state.show({
      kind: "messages",
      title: `Recherche : ${query.slice(0, 80)}`,
      items: headers.map(headerView)
    });
    if (headers.length === 0) return "Aucun message ne correspond à cette recherche.";
    return asUntrusted("en-têtes", headers.map(headerLine).join("\n---\n"));
  }

  private async read(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const messageId = arguments_.message_id;
    if (typeof messageId !== "string") return "Identifiant de message manquant.";
    const message = await this.mailbox.getMessage(state.accountId, messageId);
    state.remember([message.header]);
    state.show(messageView(message));
    return asUntrusted("message", `${headerLine(message.header)}\ncorps:\n${message.body_text}`);
  }

  private async summarize(_arguments: Record<string, unknown>, state: TurnState): Promise<string> {
    const stats = await this.mailbox.stats(state.accountId);
    const headers = await this.mailbox.listRecent(state.accountId, 15);
    state.remember(headers);
    state.show(statsView(stats));
    const folders = stats.folders.map(([name, total, unread]) => `${name} (${total}, ${unread} non lus)`).join(", ");
    const briefing = [
      `Boîte : ${stats.mailbox}`,
      `Total : ${stats.total_messages}, non lus : ${stats.unread_messages}`,
      `Dossiers : ${folders}`,
      "",
      asUntrusted("en-têtes", headers.map(headerLine).join("\n---\n"))
    ].join("\n");
    return this.delegate(SUMMARIZER_SYSTEM_PROMPT, briefing);
  }

  private async analyze(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const messageId = arguments_.message_id;
    if (typeof messageId !== "string") return "Identifiant de message manquant.";
    const message = await this.mailbox.getMessage(state.accountId, messageId);
    state.remember([message.header]);
    state.show(messageView(message));
    return this.delegate(
      ANALYST_SYSTEM_PROMPT,
      asUntrusted("message", `${headerLine(message.header)}\ncorps:\n${message.body_text}`)
    );
  }

  private async requestDelete(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const messageId = arguments_.message_id;
    if (typeof messageId !== "string") return "Identifiant de message manquant.";
    const target = state.target(messageId);
    if (!state.approvedDeletions.has(messageId)) {
      if (state.pending.every((entry) => entry.message_id !== messageId)) {
        state.pending.push(target);
      }
      return (
        "Suppression non effectuée : elle attend la confirmation explicite de l'opérateur " +
        "dans l'interface. Annonce la proposition et arrête-toi là."
      );
    }
    await this.mailbox.moveToTrash(state.accountId, messageId);
    state.deleted.push(target);
    return "Message déplacé vers les éléments supprimés ; il reste récupérable depuis Outlook.";
  }

  private async uiCatalog(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    if (this.ui === undefined) return "Le catalogue d'interface n'est pas disponible : réponds en texte.";
    const capabilities = arguments_.capabilities;
    const limit = arguments_.limit;
    let components: CatalogComponent[];
    try {
      components = await this.ui.catalog(state.accountId, {
        query: optionalStr(arguments_, "query"),
        capabilities: Array.isArray(capabilities)
          ? capabilities.filter((entry): entry is string => typeof entry === "string")
          : undefined,
        limit: typeof limit === "number" && Number.isInteger(limit) ? limit : undefined
      });
    } catch (error) {
      if (error instanceof AgentUiError) {
        return `Catalogue indisponible (code ${error.code}). Réponds en texte.`;
      }
      throw error;
    }
    for (const component of components) state.catalog.set(component.id, component);
    if (components.length === 0) return "Aucun composant disponible pour cette session : réponds en texte.";
    return JSON.stringify({ components: components.map(catalogPromptPayload) }).slice(0, 12_000);
  }

  private async uiRender(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    if (this.ui === undefined) return "L'interface n'est pas disponible : réponds en texte.";
    const componentId = optionalStr(arguments_, "component_id");
    const fallbackText = optionalStr(arguments_, "fallback_text");
    if (componentId === undefined || fallbackText === undefined) {
      return "component_id et fallback_text sont obligatoires.";
    }
    const props = arguments_.props;
    let spec;
    try {
      spec = await this.ui.render(state.accountId, {
        componentId,
        componentVersion: optionalStr(arguments_, "component_version"),
        props: asRecord(props) !== undefined ? stripSessionKeys(props as Record<string, unknown>) : {},
        data: dataSource(arguments_.data),
        fallbackText: fallbackText.slice(0, 2_000)
      });
    } catch (error) {
      if (error instanceof AgentUiError) return uiFailure(error);
      throw error;
    }
    if (
      !state.emit({
        kind: "ui.render",
        protocolVersion: PROTOCOL_VERSION,
        id: newMessageId(),
        role: "assistant",
        createdAt: nowIso(),
        ui: spec
      })
    ) {
      return "Trop de composants dans cette réponse : conclus en texte.";
    }
    state.ledger.declare(spec.instanceId, spec.componentId, spec.componentVersion);
    return JSON.stringify({
      status: "rendered",
      instance_id: spec.instanceId,
      component_id: spec.componentId,
      component_version: spec.componentVersion,
      note: "Le composant est affiché et charge ses données côté interface. Ne répète pas son contenu en texte."
    });
  }

  private async uiUpdate(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    if (this.ui === undefined) return "L'interface n'est pas disponible : réponds en texte.";
    const instanceId = optionalStr(arguments_, "instance_id");
    if (instanceId === undefined) return "instance_id est obligatoire.";
    const known = state.ledger.lookup(instanceId);
    if (known === undefined) {
      return "Instance inconnue : elle n'est plus affichée. Affiche un composant à jour.";
    }
    const patch = arguments_.patch;
    const patchRecord = asRecord(patch);
    let props = patchRecord !== undefined ? asRecord(patchRecord.props) : undefined;
    if (props === undefined) props = patchRecord;
    if (props === undefined || Object.keys(props).length === 0) {
      return "patch.props doit contenir au moins une prop.";
    }
    const [componentId, componentVersion] = known;
    let updated;
    try {
      updated = await this.ui.patch(state.accountId, {
        instanceId,
        componentId,
        componentVersion,
        props: stripSessionKeys(props)
      });
    } catch (error) {
      if (error instanceof AgentUiError) return uiFailure(error);
      throw error;
    }
    if (
      !state.emit({
        kind: "ui.patch",
        protocolVersion: PROTOCOL_VERSION,
        id: newMessageId(),
        role: "assistant",
        createdAt: nowIso(),
        ui: updated
      })
    ) {
      return "Trop de mises à jour dans cette réponse : conclus en texte.";
    }
    return JSON.stringify({ status: "patched", instance_id: updated.instanceId });
  }

  private async uiRemove(arguments_: Record<string, unknown>, state: TurnState): Promise<string> {
    const instanceId = optionalStr(arguments_, "instance_id");
    if (instanceId === undefined) return "instance_id est obligatoire.";
    if (state.ledger.lookup(instanceId) === undefined) return "Instance inconnue : rien à retirer.";
    if (
      !state.emit({
        kind: "ui.remove",
        protocolVersion: PROTOCOL_VERSION,
        id: newMessageId(),
        role: "assistant",
        createdAt: nowIso(),
        ui: { instanceId }
      })
    ) {
      return "Trop d'instructions d'interface dans cette réponse.";
    }
    state.ledger.known.delete(instanceId);
    return JSON.stringify({ status: "removed", instance_id: instanceId });
  }

  private async frameAction(event: UiActionEvent, state: TurnState): Promise<string | undefined> {
    if (this.ui === undefined) return undefined;
    let components: CatalogComponent[];
    try {
      components = await this.ui.catalog(state.accountId);
    } catch (error) {
      if (error instanceof AgentUiError) return undefined;
      throw error;
    }
    for (const entry of components) state.catalog.set(entry.id, entry);
    const declared = state.catalog.get(event.component_id);
    if (declared === undefined || !declared.allowedActions.includes(event.action_id)) {
      return undefined;
    }
    state.ledger.declare(event.instance_id, event.component_id, event.component_version);
    const payload = JSON.stringify({
      instance_id: event.instance_id,
      component_id: event.component_id,
      action_id: event.action_id,
      values: event.values,
      result: event.result
    }).slice(0, 4_000);
    return [
      "L'opérateur a interagi avec un composant affiché. Réagis : mets l'instance à jour " +
        "avec update_ui_component, affiche le composant attendu, ou réponds en texte. " +
        "N'exécute aucune action sensible sans confirmation vérifiée.",
      asUntrusted("action d'interface", payload)
    ].join("\n");
  }

  private async delegate(systemPrompt: string, briefing: string): Promise<string> {
    const response = await this.subAgent.invoke([
      { role: "system", content: systemPrompt },
      { role: "user", content: briefing }
    ]);
    return response.content || "Le sous-agent n'a produit aucune synthèse.";
  }
}

export { type ChatModel, type ChatModelFactory };
