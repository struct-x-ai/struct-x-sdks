import { defineConfig } from 'tsup';

// Dual ESM + CJS output so the SDK works with `import` (modern), with
// `require` (legacy projects + ts-node + jest with default configs),
// and with edge runtimes (Vercel, Cloudflare Workers) that prefer ESM.
//
// `dts: true` emits index.d.ts; consumers see typed everything from
// the moment they install. `splitting: false` keeps the output a
// single file per format — simpler tracing for support cases.
//
// `clean: true` wipes dist/ on every build so stale artifacts don't
// ship in a tarball.
export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm', 'cjs'],
  dts: true,
  splitting: false,
  sourcemap: true,
  clean: true,
  minify: false,
  target: 'node18',
  outExtension({ format }) {
    return { js: format === 'cjs' ? '.cjs' : '.mjs' };
  },
});
