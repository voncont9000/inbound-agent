export const START_LEAD = 0.15;
export const LEAD_STEP = 0.05;
export const MAX_LEAD = 0.4;
export const UTTERANCE_GAP = 0.25;

export class PlaybackScheduler {
  private cursor = 0;
  private hasCursor = false;
  private currentLead = START_LEAD;
  private underrunCount = 0;

  get lead(): number {
    return this.currentLead;
  }

  get underruns(): number {
    return this.underrunCount;
  }

  schedule(now: number, duration: number): number {
    let startAt: number;
    if (this.hasCursor && this.cursor >= now) {
      startAt = this.cursor;
    } else if (this.hasCursor && this.cursor > 0 && now - this.cursor <= UTTERANCE_GAP) {
      this.currentLead = Math.min(this.currentLead + LEAD_STEP, MAX_LEAD);
      this.underrunCount++;
      startAt = now + this.currentLead;
    } else {
      startAt = now + this.currentLead;
    }
    this.cursor = startAt + duration;
    this.hasCursor = true;
    return startAt;
  }

  reset(now: number): void {
    this.cursor = now;
    this.hasCursor = false;
  }
}
