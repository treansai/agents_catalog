import { Transform } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  ArrayUnique,
  IsArray,
  IsBoolean,
  IsEnum,
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  Matches,
  Max,
  MaxLength,
  Min,
  MinLength,
} from "class-validator";

import { EMAIL_CATEGORIES, PRIORITIES, type EmailCategory, type Priority } from "../domain/models";

const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const ANALYSIS_ID = /^[a-f0-9]{64}$/;

function queryInteger(value: unknown): unknown {
  if (typeof value === "string" && /^\d+$/.test(value)) return Number(value);
  return value;
}

function queryBoolean(value: unknown): unknown {
  if (value === "true") return true;
  if (value === "false") return false;
  return value;
}

export class ListAnalysesQueryDto {
  @IsOptional()
  @Transform(({ value }) => queryInteger(value))
  @IsInt()
  @Min(1)
  @Max(100)
  limit = 20;

  @IsOptional()
  @Transform(({ value }) => queryInteger(value))
  @IsInt()
  @Min(0)
  @Max(1_000_000)
  offset = 0;

  @IsOptional()
  @IsString()
  @Matches(ACCOUNT_ID)
  account_id?: string;

  @IsOptional()
  @IsEnum(EMAIL_CATEGORIES)
  category?: EmailCategory;

  @IsOptional()
  @IsEnum(PRIORITIES)
  priority?: Priority;

  @IsOptional()
  @Transform(({ value }) => queryBoolean(value))
  @IsBoolean()
  needs_human_review?: boolean;
}

export class AccountIdParamDto {
  @IsString()
  @Matches(ACCOUNT_ID)
  accountId!: string;
}

export class AnalysisIdParamDto {
  @IsString()
  @Matches(ANALYSIS_ID)
  analysisId!: string;
}

export class SyncRequestDto {
  @IsOptional()
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(100)
  @ArrayUnique()
  @IsString({ each: true })
  @Matches(ACCOUNT_ID, { each: true })
  account_ids?: string[];

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(500)
  limit?: number;
}

const MESSAGE_ID = /^[A-Za-z0-9_\-=+/]{1,512}$/;
const SENDER_ADDRESS = /^[^\s@'"]{1,64}@[^\s@'"]{1,255}$/;
// Date seule ou instant ISO 8601 complet.
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?Z?)?$/;

export class ListMessagesQueryDto {
  @IsOptional()
  @Transform(({ value }) => queryInteger(value))
  @IsInt()
  @Min(1)
  @Max(25)
  top = 10;

  /** Recherche plein texte. Exclusive du filtrage et du tri, comme l'impose Graph. */
  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(200)
  query?: string;

  @IsOptional()
  @Transform(({ value }) => queryBoolean(value))
  @IsBoolean()
  unread_only?: boolean;

  @IsOptional()
  @IsString()
  @MaxLength(320)
  @Matches(SENDER_ADDRESS)
  from_address?: string;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  @Matches(ISO_INSTANT)
  since?: string;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  @Matches(ISO_INSTANT)
  until?: string;

  @IsOptional()
  @IsIn(["asc", "desc"])
  order?: "asc" | "desc";
}

export class SenderTallyQueryDto {
  @IsOptional()
  @Transform(({ value }) => queryInteger(value))
  @IsInt()
  @Min(1)
  @Max(25)
  sample = 25;
}

export class MessageIdQueryDto {
  @IsString()
  @Matches(MESSAGE_ID)
  message_id!: string;
}

export class MessageIdBodyDto {
  @IsString()
  @Matches(MESSAGE_ID)
  message_id!: string;
}
