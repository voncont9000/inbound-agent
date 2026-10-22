import { describe, expect, it } from "vitest";
import { PlaybackScheduler } from "./playbackScheduler";

describe("PlaybackScheduler", () => {
  it("starts the first chunk with the initial lead", () => {
    const scheduler = new PlaybackScheduler();

    expect(scheduler.schedule(2, 0.04)).toBeCloseTo(2.15);
  });

  it("keeps back-to-back chunks contiguous", () => {
    const scheduler = new PlaybackScheduler();
    const first = scheduler.schedule(2, 0.04);

    expect(scheduler.schedule(2, 0.04)).toBeCloseTo(first + 0.04);
    expect(scheduler.underruns).toBe(0);
  });

  it("increases the lead after an underrun inside an utterance", () => {
    const scheduler = new PlaybackScheduler();
    const first = scheduler.schedule(1, 0.04);
    const now = first + 0.04 + 0.01;

    expect(scheduler.schedule(now, 0.04)).toBeCloseTo(now + 0.2);
    expect(scheduler.lead).toBeCloseTo(0.2);
    expect(scheduler.underruns).toBe(1);
  });

  it("caps repeated underrun growth at the maximum lead", () => {
    const scheduler = new PlaybackScheduler();
    let start = scheduler.schedule(1, 0.04);
    let cursor = start + 0.04;

    for (let index = 0; index < 10; index++) {
      start = scheduler.schedule(cursor + 0.01, 0.04);
      cursor = start + 0.04;
    }

    expect(scheduler.lead).toBeCloseTo(0.4);
    expect(scheduler.underruns).toBe(10);
  });

  it("starts after an idle gap with the current lead without counting an underrun", () => {
    const scheduler = new PlaybackScheduler();
    const first = scheduler.schedule(1, 0.04);
    const now = first + 0.04 + 0.26;

    expect(scheduler.schedule(now, 0.04)).toBeCloseTo(now + 0.15);
    expect(scheduler.lead).toBeCloseTo(0.15);
    expect(scheduler.underruns).toBe(0);
  });

  it("starts fresh after reset while keeping its learned lead", () => {
    const scheduler = new PlaybackScheduler();
    const first = scheduler.schedule(1, 0.04);
    scheduler.schedule(first + 0.04 + 0.01, 0.04);

    scheduler.reset(3);

    expect(scheduler.schedule(3, 0.04)).toBeCloseTo(3.2);
    expect(scheduler.lead).toBeCloseTo(0.2);
    expect(scheduler.underruns).toBe(1);
  });
});
