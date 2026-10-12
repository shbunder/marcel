import { randomUUID } from 'node:crypto';
import type { Artifact, Reply, Report, ReportKind } from './frames.js';

export const TOOL_REPLY = 'reply';
export const TOOL_REPORT = 'report';

const ARTIFACT_SCHEMA = {
  type: 'object',
  properties: {
    kind: { enum: ['pr', 'branch', 'doc', 'file', 'dashboard', 'link'] },
    title: { type: 'string' },
    url: { type: 'string', description: 'Required for pr, branch and link.' },
    path: { type: 'string', description: 'Absolute path on this machine. Required for doc, file and dashboard.' },
  },
  required: ['kind', 'title'],
};

export const TOOLS = [
  {
    name: TOOL_REPLY,
    description:
      'Say something to the owner in the Marcel conversation. Short sentences, outcome first, Markdown allowed. ' +
      'Use it for brain and side-thread sessions; workers use report.',
    inputSchema: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'What to say, in Markdown.' },
        in_reply_to: { type: 'string', description: 'The message_id you are answering, when there is one.' },
      },
      required: ['text'],
    },
  },
  {
    name: TOOL_REPORT,
    description:
      'Tell Marcel how a task is going. Workers use it: started, progress (needs text), artifact (needs artifact), ' +
      'done or failed (need summary). Lead with the outcome, in short sentences.',
    inputSchema: {
      type: 'object',
      properties: {
        kind: { enum: ['started', 'progress', 'artifact', 'done', 'failed'] },
        text: { type: 'string', description: 'A progress note.' },
        summary: { type: 'string', description: 'The outcome, for done or failed.' },
        artifact: ARTIFACT_SCHEMA,
        artifacts: { type: 'array', items: ARTIFACT_SCHEMA },
      },
      required: ['kind'],
    },
  },
];

type Args = Record<string, unknown>;
export type Built<T> = { ok: true; frame: T } | { ok: false; error: string };

const isString = (v: unknown): v is string => typeof v === 'string';
const newCallId = (): string => `c_${randomUUID()}`;

export function buildReply(args: Args): Built<Reply> {
  if (!isString(args.text) || args.text.trim() === '') return { ok: false, error: 'reply needs a non-empty "text".' };
  if (args.in_reply_to !== undefined && !isString(args.in_reply_to)) return { ok: false, error: '"in_reply_to" must be a string.' };
  const frame: Reply = { type: 'reply', call_id: newCallId(), text: args.text };
  if (args.in_reply_to !== undefined) frame.in_reply_to = args.in_reply_to;
  return { ok: true, frame };
}

const KINDS: readonly string[] = ['started', 'progress', 'artifact', 'done', 'failed'];
const ARTIFACT_KINDS: readonly string[] = ['pr', 'branch', 'doc', 'file', 'dashboard', 'link'];

function buildArtifact(raw: unknown): Built<Artifact> {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return { ok: false, error: 'an artifact must be an object.' };
  const a = raw as Args;
  if (!isString(a.kind) || !ARTIFACT_KINDS.includes(a.kind)) return { ok: false, error: `an artifact "kind" must be one of ${ARTIFACT_KINDS.join(', ')}.` };
  if (!isString(a.title)) return { ok: false, error: 'an artifact needs a "title".' };
  const artifact: Artifact = { kind: a.kind as Artifact['kind'], title: a.title };
  if (a.url !== undefined) {
    if (!isString(a.url) || !URL.canParse(a.url)) return { ok: false, error: 'an artifact "url" must be a full URL.' };
    artifact.url = a.url;
  }
  if (a.path !== undefined) {
    if (!isString(a.path)) return { ok: false, error: 'an artifact "path" must be a string.' };
    artifact.path = a.path;
  }
  if (['pr', 'branch', 'link'].includes(artifact.kind) && !artifact.url) return { ok: false, error: `a ${artifact.kind} artifact needs a "url".` };
  if (['doc', 'file', 'dashboard'].includes(artifact.kind) && !artifact.path) return { ok: false, error: `a ${artifact.kind} artifact needs a "path".` };
  return { ok: true, frame: artifact };
}

export function buildReport(args: Args): Built<Report> {
  if (!isString(args.kind) || !KINDS.includes(args.kind)) return { ok: false, error: `report needs a "kind": ${KINDS.join(', ')}.` };
  const kind = args.kind as ReportKind;
  const frame: Report = { type: 'report', call_id: newCallId(), kind };
  for (const key of ['text', 'summary'] as const) {
    if (args[key] === undefined) continue;
    if (!isString(args[key])) return { ok: false, error: `"${key}" must be a string.` };
    frame[key] = args[key];
  }
  if (args.artifact !== undefined) {
    const built = buildArtifact(args.artifact);
    if (!built.ok) return built;
    frame.artifact = built.frame;
  }
  if (args.artifacts !== undefined) {
    if (!Array.isArray(args.artifacts)) return { ok: false, error: '"artifacts" must be a list.' };
    const list: Artifact[] = [];
    for (const item of args.artifacts) {
      const built = buildArtifact(item);
      if (!built.ok) return built;
      list.push(built.frame);
    }
    frame.artifacts = list;
  }
  if (kind === 'progress' && !frame.text) return { ok: false, error: 'a progress report needs "text".' };
  if (kind === 'artifact' && !frame.artifact) return { ok: false, error: 'an artifact report needs "artifact".' };
  if ((kind === 'done' || kind === 'failed') && !frame.summary) return { ok: false, error: `a ${kind} report needs "summary".` };
  return { ok: true, frame };
}
