import { describe, expect, it } from 'vitest';
import { VERSION } from '../src/version.js';

describe('version', () => {
  it('is semver', () => {
    expect(VERSION).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
