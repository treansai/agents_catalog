import type { MailMessage } from "../domain/models";

const systemOne = jest.fn();
jest.mock("@typesafe-ai/sdk", () => ({
  ...jest.requireActual("@typesafe-ai/sdk"),
  TypeSafeClient: jest.fn().mockImplementation(() => ({ systemOne }))
}));

import { MessageAnalyzer } from "./message-analyzer";

const message: MailMessage = {
  account_id: "a",
  provider: "gmail",
  provider_message_id: "m1",
  subject: "Validation du budget",
  sender_name: "Claire",
  sender_address: "claire@example.com",
  received_at: "2026-08-27T09:00:00.000Z",
  body_text: "Merci de valider le budget avant le 30 août 2026.",
  snippet: "Validation attendue."
};

function answers(overrides: Record<string, number | { choice: string; confidence: number }> = {}) {
  const merged: Record<string, unknown> = {
    category: { type: "choice", choice: "action_required", confidence: 0.9 },
    prompt_injection: { type: "noul", noul: 0.01 },
    phishing: { type: "noul", noul: 0.02 },
    urgent: { type: "noul", noul: 0.1 },
    action_required: { type: "noul", noul: 0.95 }
  };
  for (const [key, value] of Object.entries(overrides)) {
    merged[key] = typeof value === "number" ? { type: "noul", noul: value } : { type: "choice", ...value };
  }
  return { answers: merged };
}

function mockJev(route: { choice: string; confidence: number }, judgments = answers()) {
  systemOne.mockImplementation(async (request: { questions: Record<string, unknown> }) =>
    "route" in request.questions ? { answers: { route: { type: "choice", ...route } } } : judgments
  );
}

describe("MessageAnalyzer (LangGraph pipeline)", () => {
  const original = process.env.TYPESAFE_API_KEY;
  afterEach(() => {
    systemOne.mockReset();
    if (original === undefined) delete process.env.TYPESAFE_API_KEY;
    else process.env.TYPESAFE_API_KEY = original;
  });

  it("uses local rules when no TypeSafe key is configured", async () => {
    delete process.env.TYPESAFE_API_KEY;
    const analysis = await new MessageAnalyzer().analyse(message);
    expect(systemOne).not.toHaveBeenCalled();
    expect(analysis.model_id).toBe("ezer-typescript-rules-v1");
  });

  it("derives triage from Jev judgments and records the provenance", async () => {
    process.env.TYPESAFE_API_KEY = "test-key";
    mockJev({ choice: "surface", confidence: 0.9 });
    const analysis = await new MessageAnalyzer().analyse(message);
    expect(analysis.model_id).toBe("jev-latest");
    expect(analysis.pipeline_version).toBe("ezer-jev-v1");
    expect(analysis.category).toBe("action_required");
    expect(analysis.priority).toBe("high");
    expect(analysis.needs_human_review).toBe(false);
    expect(analysis.triage.confidence).toBe(0.9);
    expect(analysis.action_items[0]?.due_date).toBe("30 août 2026");
  });

  it("escalates phishing to a human regardless of the category choice", async () => {
    process.env.TYPESAFE_API_KEY = "test-key";
    mockJev(
      { choice: "auto_file", confidence: 0.95 },
      answers({ phishing: 0.97, urgent: 0.9, category: { choice: "informational", confidence: 0.8 } })
    );
    const analysis = await new MessageAnalyzer().analyse(message);
    expect(analysis.category).toBe("security");
    expect(analysis.priority).toBe("critical");
    expect(analysis.needs_human_review).toBe(true);
    expect(analysis.safety.phishing_likelihood).toBeCloseTo(0.97);
  });

  it("sends an uncertain category to human review", async () => {
    process.env.TYPESAFE_API_KEY = "test-key";
    mockJev({ choice: "surface", confidence: 0.9 }, answers({ category: { choice: "other", confidence: 0.2 }, action_required: 0.1 }));
    const analysis = await new MessageAnalyzer().analyse(message);
    expect(analysis.needs_human_review).toBe(true);
  });

  it("falls back to local rules through the graph when the service fails", async () => {
    process.env.TYPESAFE_API_KEY = "test-key";
    systemOne.mockRejectedValue(new Error("down"));
    const analysis = await new MessageAnalyzer().analyse(message);
    expect(systemOne).toHaveBeenCalledTimes(1);
    expect(analysis.model_id).toBe("ezer-typescript-rules-v1");
  });

  it("lets the Jev route decide: auto_file lowers priority, human_review escalates", async () => {
    process.env.TYPESAFE_API_KEY = "test-key";
    mockJev({ choice: "auto_file", confidence: 0.9 }, answers({ action_required: 0.05, category: { choice: "newsletter", confidence: 0.9 } }));
    const filed = await new MessageAnalyzer().analyse(message);
    expect(filed.priority).toBe("low");
    expect(filed.needs_human_review).toBe(false);
    expect(systemOne).toHaveBeenCalledTimes(2);

    mockJev({ choice: "human_review", confidence: 0.9 });
    const escalated = await new MessageAnalyzer().analyse(message);
    expect(escalated.needs_human_review).toBe(true);
  });

  it("escalates when the route decision has low confidence", async () => {
    process.env.TYPESAFE_API_KEY = "test-key";
    mockJev({ choice: "auto_file", confidence: 0.1 });
    expect((await new MessageAnalyzer().analyse(message)).needs_human_review).toBe(true);
  });

  it("falls back to rules when only the route decision fails", async () => {
    process.env.TYPESAFE_API_KEY = "test-key";
    systemOne.mockImplementation(async (request: { questions: Record<string, unknown> }) => {
      if ("route" in request.questions) throw new Error("down");
      return answers();
    });
    expect((await new MessageAnalyzer().analyse(message)).model_id).toBe("ezer-typescript-rules-v1");
  });
});
