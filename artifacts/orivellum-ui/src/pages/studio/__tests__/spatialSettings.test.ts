import { describe, it, expect } from "vitest";
import { SpatialSettingsSync, SpatialSettings } from "../spatialSettings";

const s = (enabled: boolean, mode: "subtle" | "wide" = "subtle"): SpatialSettings => ({
  enabled,
  mode,
  ambience_doc_id: null,
});

function deferred() {
  let resolve!: () => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("SpatialSettingsSync — load vs save race", () => {
  it("applies a load when no save intervened", () => {
    const sync = new SpatialSettingsSync();
    const token = sync.beginLoad();
    expect(sync.shouldApplyLoad(token)).toBe(true);
  });

  it("discards a load response when a save started after the load began", async () => {
    const sync = new SpatialSettingsSync();
    const token = sync.beginLoad(); // GET in flight…
    // User toggles the setting before the GET resolves (e.g. right after
    // selecting a Work): the eventual GET response must NOT overwrite this.
    const save = sync.save("w1", s(true), async () => {});
    expect(sync.shouldApplyLoad(token)).toBe(false);
    await save;
    expect(sync.shouldApplyLoad(token)).toBe(false);
  });

  it("a fresh load after saves settle is applied again", async () => {
    const sync = new SpatialSettingsSync();
    await sync.save("w1", s(true), async () => {});
    const token = sync.beginLoad();
    expect(sync.shouldApplyLoad(token)).toBe(true);
  });
});

describe("SpatialSettingsSync — save ordering", () => {
  it("PUTs reach the server in user-action order even when the network is slow", async () => {
    const sync = new SpatialSettingsSync();
    const started: string[] = [];
    const first = deferred();

    const p1 = sync.save("w1", s(true, "wide"), async (_w, v) => {
      started.push(`start:${v.mode}`);
      await first.promise; // first PUT hangs…
      started.push("done:wide");
    });
    const p2 = sync.save("w1", s(true, "subtle"), async (_w, v) => {
      started.push(`start:${v.mode}`);
      started.push("done:subtle");
    });

    // Second PUT must not start while the first is in flight.
    await Promise.resolve();
    expect(started).toEqual(["start:wide"]);

    first.resolve();
    await Promise.all([p1, p2]);
    expect(started).toEqual(["start:wide", "done:wide", "start:subtle", "done:subtle"]);
  });

  it("the final user action is the last value persisted", async () => {
    const sync = new SpatialSettingsSync();
    const persisted: boolean[] = [];
    const put = async (_w: string, v: SpatialSettings) => { persisted.push(v.enabled); };
    await Promise.all([
      sync.save("w1", s(true), put),
      sync.save("w1", s(false), put),
      sync.save("w1", s(true), put),
    ]);
    expect(persisted).toEqual([true, false, true]);
    expect(persisted[persisted.length - 1]).toBe(true);
  });

  it("a failed latest save reports {ok:false, latest:true} so the UI can roll back", async () => {
    const sync = new SpatialSettingsSync();
    const r = await sync.save("w1", s(true), async () => { throw new Error("500"); });
    expect(r).toEqual({ ok: false, latest: true });
  });

  it("a failed save superseded by a newer one reports latest:false (no rollback)", async () => {
    const sync = new SpatialSettingsSync();
    const gate = deferred();
    const p1 = sync.save("w1", s(true), async () => {
      await gate.promise;
      throw new Error("500");
    });
    const p2 = sync.save("w1", s(false), async () => {});
    gate.resolve();
    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1).toEqual({ ok: false, latest: false });
    expect(r2).toEqual({ ok: true, latest: true });
  });

  it("a failure does not block later saves", async () => {
    const sync = new SpatialSettingsSync();
    const persisted: string[] = [];
    await sync.save("w1", s(true), async () => { throw new Error("boom"); });
    const r = await sync.save("w1", s(true, "wide"), async (_w, v) => { persisted.push(v.mode); });
    expect(r.ok).toBe(true);
    expect(persisted).toEqual(["wide"]);
  });
});
