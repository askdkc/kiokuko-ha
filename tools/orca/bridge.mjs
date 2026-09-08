// Bundled with the pinned Orca writer; stdin/stdout are a private parent-child protocol.
import { createInterface } from 'node:readline';
import { mkdir, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { TraceWriter } from '../../.cache/orca-source/packages/core/src/writer.ts';

const writers = new Map();
const root = process.argv[2];
const reply = value => process.stdout.write(JSON.stringify(value) + '\n');
process.umask(0o077);
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  let f;
  try {
    if (Buffer.byteLength(line) > 2 ** 21) throw Error('limit');
    f = JSON.parse(line);
    if (f.version !== 1 || typeof f.id !== 'string') throw Error('protocol');
    let result;
    if (f.op === 'hello') {
      result = { protocol: 1, capture: 'hermes_middleware' };
    } else {
      if (!/^run_[0-9a-f]{32}$/.test(f.run)) throw Error('run');
      if (f.op === 'open') {
        // Refuse reuse (including symlinks); never append to an old run after a crash.
        await mkdir(join(root, f.run), { mode: 0o700 });
        const info = await lstat(root);
        if (!info.isDirectory() || info.isSymbolicLink()) throw Error('path');
        const writer = await TraceWriter.create(root, {
          runId: f.run, adapter: { id: 'hermes-middleware', version: '1.0.0' },
          argv: [], cwd: '.', orcaVersion: '0.2.3', envAllowlist: [],
        });
        writers.set(f.run, writer);
        result = await writer.append({ type: 'run.start', actor: 'harness',
          attrs: { capture: 'hermes_middleware', replayable: false } });
      } else {
        const writer = writers.get(f.run);
        if (!writer) throw Error('missing');
        if (f.op === 'append') {
          if (f.event.occurredAt) f.event.occurredAt = new Date(f.event.occurredAt);
          result = await writer.append(f.event);
        }
        else if (f.op === 'close') {
          await writer.append({ type: 'run.end', actor: 'harness' });
          result = await writer.close();
          writers.delete(f.run);
        } else throw Error('operation');
      }
    }
    // Never echo a payload or raw exception into the control channel.
    reply({ version: 1, id: f.id, ok: true,
      seq: result?.seq, integrity: result?.integrity, protocol: result?.protocol });
  } catch {
    reply({ version: 1, id: f?.id ?? '', ok: false, error: 'ORCA_WRITE_FAILED' });
  }
}
