import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsIn,
  IsObject,
  IsOptional,
  IsString,
  Matches,
  MaxLength,
  MinLength,
  ValidateNested
} from "class-validator";

const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const AGENT_UI_ID = /^[A-Za-z0-9._:-]{1,128}$/;
const AGENT_UI_VERSION = /^[A-Za-z0-9._:-]{1,32}$/;
const MESSAGE_ID = /^[A-Za-z0-9_\-=+/]{1,512}$/;

export class AssistantTurnDto {
  @IsIn(["user", "assistant"])
  role!: "user" | "assistant";

  @IsString()
  @MinLength(1)
  @MaxLength(8_000)
  content!: string;
}

export class UiInstanceRefDto {
  @IsString()
  @Matches(AGENT_UI_ID)
  instance_id!: string;

  @IsString()
  @Matches(AGENT_UI_ID)
  component_id!: string;

  @IsOptional()
  @IsString()
  @Matches(AGENT_UI_VERSION)
  component_version = "1.0";
}

export class UiActionEventDto {
  @IsIn(["ui.action"])
  kind!: "ui.action";

  @IsString()
  @Matches(AGENT_UI_ID)
  event_id!: string;

  @IsString()
  @Matches(AGENT_UI_ID)
  message_id!: string;

  @IsString()
  @Matches(AGENT_UI_ID)
  instance_id!: string;

  @IsString()
  @Matches(AGENT_UI_ID)
  component_id!: string;

  @IsOptional()
  @IsString()
  @Matches(AGENT_UI_VERSION)
  component_version = "1.0";

  @IsString()
  @Matches(AGENT_UI_ID)
  action_id!: string;

  @IsObject()
  values: Record<string, unknown> = {};

  @IsString()
  @Matches(AGENT_UI_ID)
  idempotency_key!: string;

  @IsOptional()
  @IsObject()
  result?: Record<string, unknown>;
}

export class AssistantRequestDto {
  @IsString()
  @Matches(ACCOUNT_ID)
  account_id!: string;

  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(40)
  @ValidateNested({ each: true })
  @Type(() => AssistantTurnDto)
  messages!: AssistantTurnDto[];

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(20)
  @IsString({ each: true })
  @Matches(MESSAGE_ID, { each: true })
  approved_deletions: string[] = [];

  @IsOptional()
  @ValidateNested()
  @Type(() => UiActionEventDto)
  ui_action?: UiActionEventDto;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(24)
  @ValidateNested({ each: true })
  @Type(() => UiInstanceRefDto)
  ui_instances?: UiInstanceRefDto[];
}
