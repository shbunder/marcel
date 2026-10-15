// The project tracker page (S-20.2, B-46): `plugins/marcel/templates/tracker/tracker.html`, which
// a Kickoff quest publishes as a claude.ai artifact. It runs here against a small stand-in for the
// browser and the artifact's `db`, so a script error or a lost column fails the gate, and its CSS is
// checked for fixed widths a phone could not show.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';

const PAGE = readFileSync(
  fileURLToPath(new URL('../../templates/tracker/tracker.html', import.meta.url)),
  'utf8',
);
const SCRIPT = /<script>([\s\S]*)<\/script>/.exec(PAGE)?.[1] ?? '';
const CSS = /<style>([\s\S]*)<\/style>/.exec(PAGE)?.[1] ?? '';

type Doc = { id: string; data: () => Record<string, unknown> };
type Listener = (snap: { docs: Doc[] }) => void;

class Element {
  textContent = '';
  innerHTML = '';
  hidden = false;
  style: Record<string, string> = {};
  onclick: (() => void) | null = null;
}

async function page(collections: Record<string, Record<string, Record<string, unknown>>>) {
  const elements = new Map<string, Element>();
  const document = {
    title: '',
    getElementById: (id: string) => {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    addEventListener: () => {},
  };
  const snapshot = (name: string) => ({
    docs: Object.entries(collections[name] ?? {}).map(([id, data]) => ({ id, data: () => data })),
  });
  const collection = (name: string) => {
    const query = {
      orderBy: () => query,
      limit: () => query,
      onSnapshot: (on: Listener) => on(snapshot(name)),
    };
    return query;
  };
  const window = { claude: { use: async (cap: string) => (cap === 'db' ? { collection } : null) } };
  const context: { done?: Promise<void> } & Record<string, unknown> = { window, document, Date };
  // The page's script is one async function: wait for it, so a script error fails the test.
  runInNewContext(SCRIPT.replace('(async function', 'globalThis.done = (async function'), context);
  await context.done;
  return { elements, document };
}

const NOW = new Date().toISOString();
const TRACKER = {
  meta: { project: { name: 'Garden planner', repo: 'shbunder/garden-planner', goal: 'Plan the spring.' } },
  cards: {
    tsk_1: { title: 'Fix flaky login test', quest: 'Fix flaky login test', status: 'In Progress', updated_at: NOW },
    tsk_2: {
      title: 'Add dark mode', status: 'Review', pr_url: 'https://github.com/a/b/pull/14', ci: 'passing',
      waiting: true, updated_at: NOW,
    },
    tsk_3: { title: '<b>Kickoff</b>', status: 'Done', pr_url: 'javascript:alert(1)', updated_at: NOW },
  },
  log: {},
};

describe('the tracker page', () => {
  it('shows each card in its column and what waits on you, with no script error', async () => {
    const p = await page(TRACKER);
    const board = p.elements.get('board')?.innerHTML ?? '';
    const columns = board.split('<div class="col">').slice(1);
    expect(columns).toHaveLength(4);
    expect(columns[0]).toContain('TO DO');
    expect(columns[1]).toContain('Fix flaky login test');
    expect(columns[2]).toContain('Add dark mode');
    expect(columns[2]).toContain('CHECKS PASSING');
    expect(columns[3]).toContain('&lt;b&gt;Kickoff&lt;/b&gt;'); // titles are escaped
    expect(board).not.toContain('javascript:');
    expect(p.elements.get('waiting')?.innerHTML).toContain('Add dark mode');
    expect(p.elements.get('name')?.textContent).toBe('Garden planner');
    expect(p.elements.get('sub')?.textContent).toBe('shbunder/garden-planner · Plan the spring.');
    expect(p.document.title).toBe('Garden planner · tracker');
  });

  it('says so when it has no cards yet, and when its data is not there', async () => {
    const empty = await page({ meta: {}, cards: {}, log: {} });
    expect(empty.elements.get('waiting')?.innerHTML).toContain('Nothing waits on you.');
    expect(empty.elements.get('sub')?.textContent).toContain('No cards yet');

    const elements = new Map<string, Element>();
    const context: { done?: Promise<void> } & Record<string, unknown> = {
      window: { claude: { use: async () => null } },
      document: { getElementById: (id: string) => elements.get(id) ?? elements.set(id, new Element()).get(id), addEventListener: () => {} },
      Date,
    };
    runInNewContext(SCRIPT.replace('(async function', 'globalThis.done = (async function'), context);
    await context.done;
    expect(elements.get('sub')?.textContent).toContain("isn't available in this view");
  });

  it('fixes no width over 400 px, so it fits a phone', () => {
    const fixed: string[] = [];
    for (const m of CSS.matchAll(/(?<![-\w])(width|min-width|flex-basis|grid-template-columns|grid-auto-columns)\s*:\s*([^;}]+)/g)) {
      for (const px of m[2]?.matchAll(/(\d+(?:\.\d+)?)px/g) ?? []) {
        if (Number(px[1]) > 400) fixed.push(`${m[1]}: ${m[2]?.trim()}`);
      }
    }
    expect(fixed).toEqual([]);
  });
});
