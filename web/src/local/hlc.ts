// Hybrid logical clock — twin of shoebox/hlc.py: "<wall_ms:013d>-<counter:04d>-<device_id>".
export class HLC {
  private wall = 0
  private counter = 0
  readonly deviceId: string
  private clock: () => number

  constructor(deviceId: string, last?: string | null, clock: () => number = () => Date.now()) {
    this.deviceId = deviceId
    this.clock = clock
    if (last) {
      const [w, c] = last.split('-')
      this.wall = Number(w)
      this.counter = Number(c)
    }
  }

  get last(): string {
    return `${String(this.wall).padStart(13, '0')}-${String(this.counter).padStart(4, '0')}-${this.deviceId}`
  }

  now(): string {
    const physical = this.clock()
    if (physical > this.wall) { this.wall = physical; this.counter = 0 } else this.bump()
    return this.last
  }

  observe(remote: string) {
    const [w, c] = remote.split('-')
    const rw = Number(w), rc = Number(c)
    if (rw > this.wall) { this.wall = rw; this.counter = rc } else if (rw === this.wall && rc > this.counter) this.counter = rc
  }

  private bump() {
    if (this.counter >= 9999) { this.wall += 1; this.counter = 0 } else this.counter += 1
  }
}
