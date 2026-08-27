import { Transform } from "class-transformer";
import {
  ArrayMaxSize,
  IsArray,
  IsInt,
  IsObject,
  IsOptional,
  IsString,
  Matches,
  Max,
  MaxLength,
  Min
} from "class-validator";

const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const TOKEN = /^[A-Za-z0-9._:-]{1,128}$/;

export class WorkspaceQueryDto {
  @IsString()
  @Matches(ACCOUNT_ID)
  workspace_id!: string;
}

export class ResolveBodyDto {
  @IsString()
  @Matches(TOKEN)
  resolverId!: string;

  @IsObject()
  input!: Record<string, unknown>;

  @IsString()
  @Matches(TOKEN)
  componentId!: string;

  @IsString()
  @Matches(TOKEN)
  instanceId!: string;
}

export class ActionBodyDto {
  @IsString()
  kind!: string;

  @IsOptional()
  @IsString()
  @Matches(/^1\.0$/)
  protocolVersion?: string;

  @IsString()
  @Matches(TOKEN)
  eventId!: string;

  @IsString()
  @Matches(TOKEN)
  messageId!: string;

  @IsString()
  @Matches(TOKEN)
  instanceId!: string;

  @IsString()
  @Matches(TOKEN)
  componentId!: string;

  @IsString()
  @Matches(TOKEN)
  componentVersion!: string;

  @IsString()
  @Matches(TOKEN)
  actionId!: string;

  @IsObject()
  values!: Record<string, unknown>;

  @IsString()
  @Matches(TOKEN)
  idempotencyKey!: string;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  createdAt?: string;
}

export class CatalogQueryDto extends WorkspaceQueryDto {
  @IsOptional()
  @Transform(({ value }) => (typeof value === "string" && /^\d+$/.test(value) ? Number(value) : value))
  @IsInt()
  @Min(1)
  @Max(100)
  limit?: number;
}

export class RenderBodyDto {
  @IsString()
  @Matches(TOKEN)
  componentId!: string;

  @IsOptional()
  @IsString()
  @Matches(TOKEN)
  componentVersion?: string;

  @IsOptional()
  @IsObject()
  props?: Record<string, unknown>;

  @IsOptional()
  data?: unknown;

  @IsString()
  @MaxLength(2_000)
  fallbackText!: string;
}

export class PatchBodyDto {
  @IsString()
  @Matches(TOKEN)
  instanceId!: string;

  @IsString()
  @Matches(TOKEN)
  componentId!: string;

  @IsOptional()
  @IsString()
  @Matches(TOKEN)
  componentVersion?: string;

  @IsObject()
  props!: Record<string, unknown>;
}

export class CatalogSearchQueryDto extends WorkspaceQueryDto {
  @IsOptional()
  @IsString()
  @MaxLength(200)
  query?: string;

  @IsOptional()
  @Transform(({ value }) =>
    typeof value === "string" ? value.split(",").map((entry) => entry.trim()).filter(Boolean) : value
  )
  @IsArray()
  @ArrayMaxSize(12)
  @IsString({ each: true })
  @MaxLength(64, { each: true })
  capabilities?: string[];

  @IsOptional()
  @Transform(({ value }) => (typeof value === "string" && /^\d+$/.test(value) ? Number(value) : value))
  @IsInt()
  @Min(1)
  @Max(50)
  limit?: number;
}
