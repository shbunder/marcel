// Random ids, secret hashes and base64url, with the Workers runtime's Web Crypto only.

export function base64url(bytes: Uint8Array): string {
  let text = '';
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

export function randomBytes(n: number): Uint8Array {
  return crypto.getRandomValues(new Uint8Array(n));
}

/** 22 base64url characters: 132 random bits. */
export function newHandle(): string {
  return base64url(randomBytes(17)).slice(0, 22);
}

/** 32 random bytes as base64url without padding: 43 characters. */
export function newSendSecret(): string {
  return base64url(randomBytes(32));
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
