import { assertSchema, SchemaValidationError } from "./schema";
import { parseActionEvent, parseDataSource, parseRenderSpec } from "./contracts";
import { catalogForPermissions, getComponentDefinition } from "./catalog";

describe("agent-ui contracts", () => {
  it("rejects unknown keys and prototype pollution", () => {
    const polluted = JSON.parse('{"a":1,"__proto__":{"x":1},"constructor":{"prototype":{"p":true}}}') as Record<
      string,
      unknown
    >;
    expect(() =>
      assertSchema(polluted, { type: "object", additionalProperties: false, properties: { a: { type: "number" } } })
    ).toThrow(SchemaValidationError);
  });

  it("requires fallbackText on a render spec", () => {
    expect(() =>
      parseRenderSpec({
        instanceId: "i1",
        componentId: "mail.list",
        componentVersion: "1.0",
        props: {}
      })
    ).toThrow(/fallbackText/);
  });

  it("strips workspace fields from resolver input", () => {
    const source = parseDataSource({
      mode: "resolver",
      resolverId: "invoices.search",
      input: { query: "facture", workspaceId: "evil", account_id: "other", limit: 20 }
    });
    expect(source).toEqual({
      mode: "resolver",
      resolverId: "invoices.search",
      input: { query: "facture", limit: 20 }
    });
  });

  it("rejects an action without a stable idempotency key", () => {
    expect(() =>
      parseActionEvent({
        kind: "ui.action",
        eventId: "e1",
        messageId: "m1",
        instanceId: "i1",
        componentId: "mail.list",
        componentVersion: "1.0",
        actionId: "messages.open",
        values: {},
        idempotencyKey: ""
      })
    ).toThrow();
  });

  it("does not expose import paths in the catalog", () => {
    const catalog = catalogForPermissions(["mail.read"]);
    expect(catalog.some((entry) => entry.id === "mail.list")).toBe(true);
    const serialized = JSON.stringify(catalog);
    expect(serialized).not.toMatch(/import\(|components\/|ezer-front|graph\.microsoft/);
    expect(getComponentDefinition("not-a-component")).toBeUndefined();
  });
});
