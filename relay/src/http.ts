// Answers in app-api.yaml's `Error` shape (contracts/push-relay.yaml § Errors).

export type ErrorCode =
  | 'bad_request'
  | 'unauthorized'
  | 'not_found'
  | 'handle_gone'
  | 'too_large'
  | 'too_many_pushes'
  | 'too_many_registrations'
  | 'apns_unavailable';

const STATUS: Record<ErrorCode, number> = {
  bad_request: 400,
  unauthorized: 401,
  not_found: 404,
  handle_gone: 410,
  too_large: 413,
  too_many_pushes: 429,
  too_many_registrations: 429,
  apns_unavailable: 502,
};

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

export function error(code: ErrorCode, message: string): Response {
  return json(STATUS[code], { code, message });
}

export const UNAUTHORIZED = (): Response =>
  // The same words whether the handle exists or not, so a caller cannot probe for handles.
  error('unauthorized', 'Not this handle’s send secret.');
