/**
 * A number in [0, 1) derived from a string (FNV-1a hash). The same text always gives the same
 * number, on every machine, so choices made with it (like which floors have lit windows) never
 * change between visits. Never use Math.random() for anything that should look the same twice.
 */
export function hash01(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    // Math.imul multiplies as 32-bit integers, like the C code FNV was designed for.
    hash = Math.imul(hash, 0x01000193);
  }
  // `>>> 0` reinterprets the result as an unsigned 32-bit number.
  return (hash >>> 0) / 4294967296;
}
