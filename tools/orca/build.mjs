import { build } from '../../.cache/orca-source/node_modules/esbuild/lib/main.js';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
const root = resolve('.cache/orca-source');
await build({
  entryPoints: ['tools/orca/bridge.mjs'], bundle: true, platform: 'node', format: 'esm',
  target: 'node22', outfile: 'src/hermes_kiokuko/orca/bridge.mjs', minify: true,
  banner: { js: "import { createRequire as __bundleRequire } from 'node:module';const require=__bundleRequire(import.meta.url);" },
  alias: { '@orcareplay/schema': root + '/packages/schema/src/index.ts',
           '@orcareplay/plugin-api': root + '/packages/plugin-api/src/index.ts' },
  plugins: [{ name: 'embed-orca-schemas', setup(b) {
    b.onLoad({ filter: /schema\/src\/validate\.ts$/ }, args => ({ loader: 'ts',
      contents: readFileSync(args.path, 'utf8')
        .replace("import { createRequire } from 'node:module';", '')
        .replace('const require = createRequire(import.meta.url);', '')
        .replace("const eventSchema = require('../schema/event.schema.json') as Record<string, unknown>;", "import eventSchema from '../schema/event.schema.json';")
        .replace("const manifestSchema = require('../schema/manifest.schema.json') as Record<string, unknown>;", "import manifestSchema from '../schema/manifest.schema.json';")
    }));
  }}],
});
