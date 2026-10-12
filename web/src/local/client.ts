// Main-thread handle to the on-device worker (promise RPC).
type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void }

class LocalClient {
  private worker: Worker | null = null
  private seq = 0
  private pending = new Map<number, Pending>()

  private ensure(): Worker {
    if (!this.worker) {
      this.worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' })
      this.worker.onmessage = (e: MessageEvent<{ id: number; result?: unknown; error?: string }>) => {
        const p = this.pending.get(e.data.id)
        if (!p) return
        this.pending.delete(e.data.id)
        if (e.data.error) p.reject(new Error(e.data.error))
        else p.resolve(e.data.result)
      }
    }
    return this.worker
  }

  call<T>(op: string, ...args: unknown[]): Promise<T> {
    const id = ++this.seq
    const transfer = args.filter((a): a is Uint8Array => a instanceof Uint8Array).map((a) => a.buffer)
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject })
      this.ensure().postMessage({ id, op, args }, transfer as Transferable[])
    })
  }
}

export const local = new LocalClient()

export async function gunzip(data: ArrayBuffer | Blob): Promise<Uint8Array> {
  const stream = (data instanceof Blob ? data : new Blob([data])).stream().pipeThrough(new DecompressionStream('gzip'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

// For diagnostics and browser tests.
;(globalThis as Record<string, unknown>).photoctlLocal = { local, gunzip }
