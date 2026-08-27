/**
 * Validateur JSON Schema minimal — identique à l'esprit du backend, sans Buffer.
 */

export type JsonSchema = {
  type?: "object" | "array" | "string" | "number" | "integer" | "boolean" | "null";
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean | JsonSchema;
  items?: JsonSchema;
  enum?: Array<string | number | boolean>;
  const?: unknown;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  description?: string;
  anyOf?: JsonSchema[];
};

export class SchemaValidationError extends Error {
  readonly path: string;

  constructor(path: string, message: string) {
    super(`${path}: ${message}`);
    this.name = "SchemaValidationError";
    this.path = path;
  }
}

const FORBIDDEN_KEYS = new Set(["__proto__", "prototype", "constructor"]);
const MAX_DEPTH = 8;
const MAX_KEYS = 64;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function jsonSize(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).length;
}

export function assertSchema(value: unknown, schema: JsonSchema, path = "$"): void {
  if (schema.const !== undefined) {
    if (JSON.stringify(value) !== JSON.stringify(schema.const)) {
      throw new SchemaValidationError(path, "unexpected value");
    }
    return;
  }
  if (schema.anyOf !== undefined) {
    const errors: string[] = [];
    for (const option of schema.anyOf) {
      try {
        assertSchema(value, option, path);
        return;
      } catch (error) {
        errors.push(error instanceof Error ? error.message : "invalid");
      }
    }
    throw new SchemaValidationError(path, `no alternative matched (${errors.join("; ")})`);
  }
  if (schema.enum !== undefined && !schema.enum.some((entry) => entry === value)) {
    throw new SchemaValidationError(path, "value is not in the enum");
  }
  switch (schema.type) {
    case "null":
      if (value !== null) throw new SchemaValidationError(path, "expected null");
      return;
    case "boolean":
      if (typeof value !== "boolean") throw new SchemaValidationError(path, "expected boolean");
      return;
    case "integer":
      if (typeof value !== "number" || !Number.isSafeInteger(value)) {
        throw new SchemaValidationError(path, "expected integer");
      }
      assertNumericBounds(value, schema, path);
      return;
    case "number":
      if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new SchemaValidationError(path, "expected number");
      }
      assertNumericBounds(value, schema, path);
      return;
    case "string":
      if (typeof value !== "string") throw new SchemaValidationError(path, "expected string");
      if (schema.minLength !== undefined && value.length < schema.minLength) {
        throw new SchemaValidationError(path, "string too short");
      }
      if (schema.maxLength !== undefined && value.length > schema.maxLength) {
        throw new SchemaValidationError(path, "string too long");
      }
      if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
        throw new SchemaValidationError(path, "string does not match pattern");
      }
      return;
    case "array":
      if (!Array.isArray(value)) throw new SchemaValidationError(path, "expected array");
      if (schema.minItems !== undefined && value.length < schema.minItems) {
        throw new SchemaValidationError(path, "array too short");
      }
      if (schema.maxItems !== undefined && value.length > schema.maxItems) {
        throw new SchemaValidationError(path, "array too long");
      }
      if (schema.items !== undefined) {
        value.forEach((entry, index) =>
          assertSchema(entry, schema.items as JsonSchema, `${path}[${index}]`),
        );
      }
      return;
    case "object":
      assertObject(value, schema, path, 0);
      return;
    default:
      if (schema.properties !== undefined || schema.additionalProperties !== undefined) {
        assertObject(value, schema, path, 0);
      }
  }
}

function assertNumericBounds(value: number, schema: JsonSchema, path: string): void {
  if (schema.minimum !== undefined && value < schema.minimum) {
    throw new SchemaValidationError(path, "number below minimum");
  }
  if (schema.maximum !== undefined && value > schema.maximum) {
    throw new SchemaValidationError(path, "number above maximum");
  }
}

function assertObject(value: unknown, schema: JsonSchema, path: string, depth: number): void {
  if (depth > MAX_DEPTH) throw new SchemaValidationError(path, "object too deep");
  if (!isRecord(value)) throw new SchemaValidationError(path, "expected object");
  const keys = Object.keys(value);
  if (keys.length > MAX_KEYS) throw new SchemaValidationError(path, "too many keys");
  for (const key of keys) {
    if (FORBIDDEN_KEYS.has(key)) throw new SchemaValidationError(`${path}.${key}`, "forbidden key");
  }
  for (const required of schema.required ?? []) {
    if (!(required in value)) throw new SchemaValidationError(path, `missing ${required}`);
  }
  const properties = schema.properties ?? {};
  for (const [key, child] of Object.entries(value)) {
    const propertySchema = properties[key];
    if (propertySchema !== undefined) {
      assertSchema(child, propertySchema, `${path}.${key}`);
      continue;
    }
    if (schema.additionalProperties === false) {
      throw new SchemaValidationError(`${path}.${key}`, "unknown property");
    }
    if (typeof schema.additionalProperties === "object") {
      assertSchema(child, schema.additionalProperties, `${path}.${key}`);
    }
  }
}
