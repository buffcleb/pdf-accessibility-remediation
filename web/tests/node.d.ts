// The little of Node's test runner the web tests use (the web app doesn't install Node's types)
declare module "node:test" {
  export function test(name: string, fn: () => void | Promise<void>): void;
}
declare module "node:assert/strict" {
  const assert: {
    equal(a: unknown, b: unknown, msg?: string): void;
    deepEqual(a: unknown, b: unknown, msg?: string): void;
    ok(v: unknown, msg?: string): void;
  };
  export default assert;
}
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
