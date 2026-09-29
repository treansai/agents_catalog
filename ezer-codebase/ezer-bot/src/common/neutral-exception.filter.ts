import { Catch, HttpException, HttpStatus, Logger, type ArgumentsHost, type ExceptionFilter } from "@nestjs/common";
import type { Request, Response } from "express";

export interface RequestWithId extends Request {
  requestId?: string;
}

function neutralMessage(status: number): string {
  if (status === HttpStatus.BAD_REQUEST || status === HttpStatus.UNPROCESSABLE_ENTITY) {
    return "invalid request";
  }
  if (status === HttpStatus.UNAUTHORIZED || status === HttpStatus.FORBIDDEN) return "unauthorized";
  if (status === HttpStatus.NOT_FOUND) return "resource not found";
  if (status === HttpStatus.PAYLOAD_TOO_LARGE) return "request too large";
  if (status === HttpStatus.SERVICE_UNAVAILABLE) return "service unavailable";
  if (status >= 500) return "internal server error";
  return "request failed";
}

@Catch()
export class NeutralExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger(NeutralExceptionFilter.name);

  catch(exception: unknown, host: ArgumentsHost): void {
    const http = host.switchToHttp();
    const request = http.getRequest<RequestWithId>();
    const response = http.getResponse<Response>();
    const status = exception instanceof HttpException ? exception.getStatus() : HttpStatus.INTERNAL_SERVER_ERROR;
    const message = neutralMessage(status);
    const requestId = request.requestId ?? "unavailable";
    if (status >= 500) {
      this.logger.error(
        JSON.stringify({
          event: "http_request_failed",
          request_id: requestId,
          method: request.method,
          path: request.path,
          error_type: exception instanceof Error ? exception.constructor.name : "UnknownError"
        })
      );
    }
    response.status(status).json({
      statusCode: status,
      message,
      detail: message,
      request_id: requestId
    });
  }
}
