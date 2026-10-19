// The v2 build writes into docs/ beside the classic page, so it cannot empty
// the output directory. Remove only what the previous v2 build produced.
import { rmSync } from 'node:fs'
import { resolve } from 'node:path'

const docs = resolve(import.meta.dirname, '../../docs')
rmSync(resolve(docs, 'v2'), { recursive: true, force: true })
rmSync(resolve(docs, 'index.html'), { force: true })
