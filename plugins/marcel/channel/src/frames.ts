// Frame types from contracts/channel.schema.json. A field the contract lacks does not exist here.

export type ReportKind = 'started' | 'progress' | 'artifact' | 'done' | 'failed';

export interface Artifact {
  kind: 'pr' | 'branch' | 'doc' | 'file' | 'dashboard' | 'link';
  title: string;
  url?: string;
  path?: string;
}

export interface Hello {
  type: 'hello';
  session_id: string;
  secret: string;
  plugin_version: string;
  resume_after?: number;
}
export interface Reply {
  type: 'reply';
  call_id: string;
  text: string;
  in_reply_to?: string;
}
export interface Report {
  type: 'report';
  call_id: string;
  kind: ReportKind;
  text?: string;
  summary?: string;
  artifact?: Artifact;
  artifacts?: Artifact[];
}
export interface PermissionRequest {
  type: 'permission_request';
  request_id: string;
  tool_name: string;
  description: string;
  input_preview?: string;
}
export interface Ack {
  type: 'ack';
  seq: number;
}
export interface Ping {
  type: 'ping';
}
export type ToHub = Hello | Reply | Report | PermissionRequest | Ack | Ping;

export type RejectReason = 'unknown_session' | 'bad_secret' | 'plugin_too_old';
export interface Welcome {
  type: 'welcome';
  role: 'brain' | 'side' | 'worker' | 'adopted';
  agent_id: string;
  conversation_id?: string;
  task_id?: string;
}
export interface Rejected {
  type: 'rejected';
  reason: RejectReason;
  message?: string;
}
export interface UserMessage {
  type: 'user_message';
  seq: number;
  message_id: string;
  conversation_id: string;
  text: string;
  quoted_message_id?: string;
}
export interface Steer {
  type: 'steer';
  seq: number;
  task_id: string;
  note_id: string;
  author: 'user' | 'marcel';
  text: string;
  handback?: boolean;
}
export interface SystemEvent {
  type: 'system_event';
  seq: number;
  kind: string;
  text: string;
  task_id?: string;
  data?: Record<string, unknown>;
}
export interface PermissionDecision {
  type: 'permission_decision';
  seq: number;
  request_id: string;
  behavior: 'allow' | 'deny';
}
export interface CallAck {
  type: 'call_ack';
  call_id: string;
  duplicate?: boolean;
}
export interface Pong {
  type: 'pong';
}

/** Frames that carry a hub `seq` and are handed to Claude Code. */
export type Deliverable = UserMessage | Steer | SystemEvent | PermissionDecision;
export type ToSession = Welcome | Rejected | Deliverable | CallAck | Pong;

/** Claude Code builds permission request ids from the letters a-z without `l`. */
export const REQUEST_ID = /^[a-km-z]{5}$/;
