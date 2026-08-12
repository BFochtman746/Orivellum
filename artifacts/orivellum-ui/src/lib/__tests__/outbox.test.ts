/**
 * Outbox (IndexedDB op store) — the client half of the iPhone continuity core.
 *
 * Runs against fake-indexeddb: covers stable op ids, latest-wins replaceKey,
 * ordered flush that stops on the first "retry", failed ops waiting for an
 * explicit retry, and change notifications.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";

// Fresh module + fresh IDB per test — the module caches its open DB promise.
async function freshOutbox() {
  indexedDB = new IDBFactory();
  vi.resetModules();
  return await import("@/lib/outbox");
}

let outbox: Awaited<ReturnType<typeof freshOutbox>>;

beforeEach(async () => {
  outbox = await freshOutbox();
});

const chatPayload = (text: string) => ({
  convId: "c1",
  text,
  deep: false,
  scope: "all" as const,
});

describe("isTransientHttpError", () => {
  it("treats the flusher's retry statuses (408/409/429/5xx) as transient", () => {
    for (const status of [408, 409, 429, 500, 502, 503, 504]) {
      expect(outbox.isTransientHttpError(new outbox.HttpError(status, "x"))).toBe(true);
    }
  });

  it("treats real rejections and unknown errors as non-transient", () => {
    for (const status of [400, 401, 403, 404, 422]) {
      expect(outbox.isTransientHttpError(new outbox.HttpError(status, "x"))).toBe(false);
    }
    expect(outbox.isTransientHttpError(new Error("AI service error: 503"))).toBe(false); // no status
    expect(outbox.isTransientHttpError(undefined)).toBe(false);
  });

  it("accepts any error object carrying a numeric status", () => {
    expect(outbox.isTransientHttpError({ status: 503 })).toBe(true);
    expect(outbox.isTransientHttpError({ status: 404 })).toBe(false);
  });
});

describe("enqueueOp", () => {
  it("persists before network with a stable caller-provided op id", async () => {
    const opId = await outbox.enqueueOp("chat_message", chatPayload("hi"), {
      opId: "my-client-msg-id",
    });
    expect(opId).toBe("my-client-msg-id");
    const op = await outbox.getOp(opId);
    expect(op?.state).toBe("queued");
    expect(op?.attempts).toBe(0);
  });

  it("replaceKey keeps only the newest op (latest-wins draft saves)", async () => {
    await outbox.enqueueOp(
      "api_call",
      { method: "PATCH", url: "/api/write/documents/d1", body: { v: 1 }, label: "Draft save" },
      { replaceKey: "write-doc-d1" },
    );
    await outbox.enqueueOp(
      "api_call",
      { method: "PATCH", url: "/api/write/documents/d1", body: { v: 2 }, label: "Draft save" },
      { replaceKey: "write-doc-d1" },
    );
    const ops = await outbox.listOps();
    expect(ops).toHaveLength(1);
    expect((ops[0].payload as { body: { v: number } }).body.v).toBe(2);
  });

  it("notifies subscribers on every mutation", async () => {
    const seen = vi.fn();
    const unsub = outbox.subscribeOutbox(seen);
    const opId = await outbox.enqueueOp("chat_message", chatPayload("x"));
    await outbox.markOpState(opId, "failed", "boom");
    await outbox.removeOp(opId);
    unsub();
    expect(seen.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});

describe("flushOutbox", () => {
  it("delivers oldest-first and removes delivered ops", async () => {
    const a = await outbox.enqueueOp("chat_message", chatPayload("first"));
    const b = await outbox.enqueueOp("chat_message", chatPayload("second"));
    const order: string[] = [];
    await outbox.flushOutbox({
      chat_message: async (op) => {
        order.push(op.opId);
        return "delivered";
      },
      api_call: async () => "delivered",
    });
    expect(order).toEqual([a, b]);
    expect(await outbox.listOps()).toHaveLength(0);
  });

  it("stops at the first retry so a later op never overtakes an earlier one", async () => {
    const a = await outbox.enqueueOp("chat_message", chatPayload("first"));
    const b = await outbox.enqueueOp("chat_message", chatPayload("second"));
    const attempted: string[] = [];
    await outbox.flushOutbox({
      chat_message: async (op) => {
        attempted.push(op.opId);
        return "retry";
      },
      api_call: async () => "delivered",
    });
    expect(attempted).toEqual([a]); // b never attempted
    const ops = await outbox.listOps();
    expect(ops.map((o) => o.opId)).toEqual([a, b]);
    expect(ops.every((o) => o.state === "queued")).toBe(true);
  });

  it("marks server-rejected ops failed and skips them on later flushes", async () => {
    const bad = await outbox.enqueueOp("chat_message", chatPayload("rejected"));
    await outbox.flushOutbox({
      chat_message: async () => "failed",
      api_call: async () => "delivered",
    });
    expect((await outbox.getOp(bad))?.state).toBe("failed");

    // A later flush must NOT auto-retry it…
    const handler = vi.fn(async () => "delivered" as const);
    await outbox.flushOutbox({ chat_message: handler, api_call: handler });
    expect(handler).not.toHaveBeenCalled();

    // …until the user explicitly retries.
    await outbox.retryOp(bad);
    await outbox.flushOutbox({ chat_message: handler, api_call: handler });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(await outbox.getOp(bad)).toBeUndefined();
  });

  it("requeues ops orphaned in 'sending' by a page death mid-flush", async () => {
    const opId = await outbox.enqueueOp("chat_message", chatPayload("orphan"));
    // Simulate the page dying between markOpState("sending") and the verdict.
    await outbox.markOpState(opId, "sending");

    const handler = vi.fn(async () => "delivered" as const);
    await outbox.flushOutbox({ chat_message: handler, api_call: handler });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(await outbox.getOp(opId)).toBeUndefined();
  });

  it("treats a thrown handler error as retry (op stays queued)", async () => {
    const opId = await outbox.enqueueOp("chat_message", chatPayload("boom"));
    await outbox.flushOutbox({
      chat_message: async () => {
        throw new TypeError("Failed to fetch");
      },
      api_call: async () => "delivered",
    });
    const op = await outbox.getOp(opId);
    expect(op?.state).toBe("queued");
    expect(op?.attempts).toBe(1);
  });
});

describe("isNetworkError", () => {
  it("classifies fetch TypeErrors and connection strings as network errors", () => {
    expect(outbox.isNetworkError(new TypeError("Failed to fetch"))).toBe(true);
    expect(outbox.isNetworkError(new Error("NetworkError when attempting"))).toBe(true);
    expect(outbox.isNetworkError(new Error("Review update failed"))).toBe(false);
  });
});
