import type {
  AccountSummary,
  DashboardSnapshot,
  EmailAnalysis,
  SyncRequest,
  SyncResponse,
} from "@/lib/ezer-types";

export const DEMO_ACCOUNTS: AccountSummary[] = [
  { id: "work-demo", provider: "outlook" },
  { id: "personal-demo", provider: "gmail" },
];

export const DEMO_ANALYSES: EmailAnalysis[] = [
  {
    analysis_id:
      "9d7d82429b92a732204d10a1c8fd4e63ac44eb8733f827d5f55fd6ab5bc06a8f",
    message_ref: "outlook:work-demo:project-orion-review",
    content_hash:
      "c3f60ae164139cc002332167a98a39c8b7049420163e077cf5b474fd98ab2ae9",
    pipeline_version: "ezer-1.0",
    model_id: "demo-model",
    prompt_version: "triage-v1",
    created_at: "2026-08-27T07:42:00.000Z",
    category: "action_required",
    priority: "high",
    needs_human_review: false,
    summary:
      "L'équipe projet demande une validation du périmètre révisé avant la réunion de vendredi.",
    key_points: [
      "Le planning proposé conserve la date de livraison initiale.",
      "Deux arbitrages fonctionnels restent à confirmer.",
    ],
    action_items: [
      {
        description: "Valider le périmètre révisé avant la réunion.",
        owner: "Vous",
        due_date: "2026-08-28",
        confidence: 0.94,
      },
    ],
    safety: {
      risk_level: "none",
      prompt_injection_detected: false,
      phishing_likelihood: 0.01,
      indicators: [],
      rationale: "Message interne cohérent, sans lien ni demande sensible.",
      confidence: 0.98,
    },
    triage: {
      category: "action_required",
      priority: "high",
      needs_human_review: false,
      confidence: 0.96,
      rationale: "Une décision explicite est attendue à brève échéance.",
    },
    detected_language: "fr",
  },
  {
    analysis_id:
      "5215f00fb466df50155927df291ea0e6f8f040b27f0943d686160709c323f3eb",
    message_ref: "gmail:personal-demo:train-booking-confirmation",
    content_hash:
      "f08e55b0fda9e25922d7679f281a22a06d058ad6f0f9d237ee419316fc00f52d",
    pipeline_version: "ezer-1.0",
    model_id: "demo-model",
    prompt_version: "triage-v1",
    created_at: "2026-08-26T16:18:00.000Z",
    category: "receipt",
    priority: "normal",
    needs_human_review: false,
    summary:
      "Confirmation d'une réservation ferroviaire aller-retour avec billets disponibles dans l'espace client.",
    key_points: [
      "Le départ est prévu lundi matin.",
      "Le billet est modifiable selon les conditions tarifaires.",
    ],
    action_items: [],
    safety: {
      risk_level: "low",
      prompt_injection_detected: false,
      phishing_likelihood: 0.04,
      indicators: [],
      rationale: "Confirmation transactionnelle attendue et sans anomalie détectée.",
      confidence: 0.95,
    },
    triage: {
      category: "receipt",
      priority: "normal",
      needs_human_review: false,
      confidence: 0.93,
      rationale: "Le message confirme une transaction déjà effectuée.",
    },
    detected_language: "fr",
  },
  {
    analysis_id:
      "167ee674d745c7e0f968f0be6177b867030aa685e1ced6bda3649b1484867dd4",
    message_ref: "outlook:work-demo:security-access-review",
    content_hash:
      "a0702a5dc3330f8b4e76fc4128af7924fd8b8f94b9a772ceda40ef1f7f4d7f1b",
    pipeline_version: "ezer-1.0",
    model_id: "demo-model",
    prompt_version: "triage-v1",
    created_at: "2026-08-26T09:05:00.000Z",
    category: "security",
    priority: "critical",
    needs_human_review: true,
    summary:
      "Une notification demande de vérifier une connexion inhabituelle depuis un nouvel appareil.",
    key_points: [
      "La localisation annoncée ne correspond pas aux habitudes récentes.",
      "Le message contient un lien de vérification externe.",
    ],
    action_items: [
      {
        description:
          "Vérifier l'activité directement depuis le portail officiel, sans utiliser le lien reçu.",
        owner: "Vous",
        due_date: null,
        confidence: 0.97,
      },
    ],
    safety: {
      risk_level: "high",
      prompt_injection_detected: false,
      phishing_likelihood: 0.78,
      indicators: [
        "Lien vers un domaine différent de celui du service annoncé",
        "Ton inhabituellement urgent",
      ],
      rationale:
        "Plusieurs signaux justifient une vérification manuelle via le site officiel.",
      confidence: 0.91,
    },
    triage: {
      category: "security",
      priority: "critical",
      needs_human_review: true,
      confidence: 0.98,
      rationale: "Le risque potentiel nécessite une action humaine immédiate.",
    },
    detected_language: "fr",
  },
  {
    analysis_id:
      "de5c60719ca8b0864a259a6541e754a4a99a6973902834832b59071a2972cb8b",
    message_ref: "gmail:personal-demo:weekly-design-digest",
    content_hash:
      "fd81d1b423f31ff9025273012448656e5787576a09f3777a6e035ba2b2608515",
    pipeline_version: "ezer-1.0",
    model_id: "demo-model",
    prompt_version: "triage-v1",
    created_at: "2026-08-25T06:30:00.000Z",
    category: "newsletter",
    priority: "low",
    needs_human_review: false,
    summary:
      "La lettre hebdomadaire présente une sélection d'articles sur l'accessibilité et les systèmes de design.",
    key_points: [
      "Un guide traite des contrastes dans les interfaces de données.",
      "Une étude de cas couvre la migration d'un design system.",
    ],
    action_items: [],
    safety: {
      risk_level: "none",
      prompt_injection_detected: false,
      phishing_likelihood: 0.01,
      indicators: [],
      rationale: "Newsletter régulière provenant d'un expéditeur attendu.",
      confidence: 0.99,
    },
    triage: {
      category: "newsletter",
      priority: "low",
      needs_human_review: false,
      confidence: 0.97,
      rationale: "Contenu éditorial sans demande d'action.",
    },
    detected_language: "fr",
  },
];

export function createDemoSnapshot(): DashboardSnapshot {
  return {
    mode: "demo",
    health: "ready",
    accounts: DEMO_ACCOUNTS.map((account) => ({ ...account })),
    analyses: DEMO_ANALYSES.map((analysis) => ({ ...analysis })),
    total: DEMO_ANALYSES.length,
    generatedAt: new Date().toISOString(),
  };
}

export function createDemoSyncResponse(request: SyncRequest): SyncResponse {
  const requestedAccounts = request.account_ids
    ? new Set(request.account_ids)
    : null;

  return {
    reports: DEMO_ACCOUNTS.filter(
      (account) => requestedAccounts === null || requestedAccounts.has(account.id),
    ).map((account) => ({
      account_id: account.id,
      provider: account.provider,
      fetched: 0,
      processed: 0,
      skipped: 0,
      failed: 0,
      dead_lettered: 0,
      cursor_advanced: false,
      cursor_reset: false,
      analyses: [],
    })),
  };
}
