// Make and validate ops on this device (schema shared with Python: spec/ops.schema.json).
import Ajv2020 from 'ajv/dist/2020'
import schema from '../../../spec/ops.schema.json'
import type { Op } from './apply'
import type { HLC } from './hlc'

const ENTITY: Record<string, [string, (b: Record<string, any>) => string]> = { // eslint-disable-line @typescript-eslint/no-explicit-any
  'media.upsert': ['media', (b) => b.record.photo_id], 'media.source': ['media', (b) => b.photo_id],
  'media.set': ['media', (b) => b.photo_id], 'media.delete': ['media', (b) => b.photo_id], 'media.restore': ['media', (b) => b.photo_id],
  'tag.add': ['media', (b) => b.photo_id], 'tag.remove': ['media', (b) => b.photo_id],
  'album.add': ['media', (b) => b.photo_id], 'album.remove': ['media', (b) => b.photo_id],
  'media.person.add': ['media', (b) => b.photo_id], 'media.person.remove': ['media', (b) => b.photo_id],
  'person.create': ['person', (b) => b.person_id], 'person.set': ['person', (b) => b.person_id], 'person.merge': ['person', (b) => b.from],
  'device.register': ['device', (b) => b.device_id], 'device.set': ['device', (b) => b.device_id],
}

const check = new Ajv2020({ allErrors: false, strict: false }).compile(schema)

export function validate(op: Op) {
  if (!check(op)) {
    const e = check.errors?.[0]
    throw new Error(`invalid ${op.kind ?? 'op'}: ${e?.instancePath || '(op)'} ${e?.message ?? ''}`.trim())
  }
}

export function makeOp(clock: HLC, kind: string, body: Record<string, unknown>): Op {
  if (!ENTITY[kind]) throw new Error(`unknown op kind ${kind}`)
  const hlc = clock.now()
  const [type, key] = ENTITY[kind]
  const op = { op_id: hlc, hlc, device_id: clock.deviceId, kind, entity: `${type}:${key(body)}`, body } as Op
  validate(op)
  return op
}
