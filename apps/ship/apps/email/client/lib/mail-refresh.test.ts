import { afterEach, beforeEach, describe, expect, it, jest } from "bun:test";
import {
  focusManager,
  onlineManager,
  InfiniteQueryObserver,
  QueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import { createStore } from "jotai";
import { optimisticActionsAtom } from "../store/optimistic-updates";
import { backgroundQueueAtom } from "../store/backgroundQueue";
import { startMailRefresh } from "./mail-refresh";

type Page = { threads: Array<{ id: string; hasUnread: boolean }>; nextPageToken: number | null };
const page = (...ids: string[]): Page => ({
  threads: ids.map((id) => ({ id, hasUnread: true })),
  nextPageToken: null,
});
const listKey = (folder = "inbox", q = "") =>
  [["mail", "listThreads"], { input: { folder, q, labelIds: [] }, type: "infinite" }] as const;

let client: QueryClient;
let store: ReturnType<typeof createStore>;
let cleanup: Array<() => void>;

beforeEach(() => {
  jest.useFakeTimers();
  focusManager.setFocused(true);
  onlineManager.setOnline(true);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  store = createStore();
  cleanup = [];
});

afterEach(() => {
  cleanup.reverse().forEach((stop) => stop());
  client.clear();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  jest.useRealTimers();
});

// Let React Query's asynchronous fetch/cache notifications settle after each tick.
async function settle() {
  for (let i = 0; i < 30; i++) await Promise.resolve();
}

async function advance(ms = 30_000) {
  jest.advanceTimersByTime(ms);
  await settle();
}

function observe(
  fetchPage: (cursor: number) => Promise<Page>,
  initial: Page[] = [page()],
  key = listKey(),
) {
  const observer = new InfiniteQueryObserver(client, {
    queryKey: key,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: 0,
    getNextPageParam: (lastPage: Page) => lastPage.nextPageToken,
    initialData: { pages: initial, pageParams: initial.map((_, index) => index) },
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  cleanup.push(observer.subscribe(() => {}));
  return observer;
}

function start() {
  const stop = startMailRefresh(client, store, [["mail", "listThreads"]]);
  cleanup.push(stop);
  return stop;
}

describe("automatic mailbox refresh (#894)", () => {
  it("shows arriving mail in an empty inbox and shares each poll across row observers", async () => {
    let delivered = page();
    let requests = 0;
    const fetchPage = async () => {
      requests++;
      return delivered;
    };
    const inbox = observe(fetchPage);
    const rowConsumer = observe(fetchPage);
    start();

    delivered = page("new-message");
    await advance(29_999);
    expect(inbox.getCurrentResult().data?.pages).toEqual([page()]);
    await advance(1);
    expect(inbox.getCurrentResult().data?.pages).toEqual([delivered]);
    expect(rowConsumer.getCurrentResult().data?.pages).toEqual([delivered]);
    expect(requests).toBe(1);

    delivered = page("newer-message", "new-message");
    await advance();
    expect(inbox.getCurrentResult().data?.pages).toEqual([delivered]);
    expect(requests).toBe(2);
  });

  it("pauses while hidden or offline and catches up on returning to the mailbox", async () => {
    let delivered = page("first");
    const inbox = observe(async () => delivered);
    start();
    focusManager.setFocused(false);
    await advance(60_000);
    expect(inbox.getCurrentResult().data?.pages).toEqual([page()]);

    focusManager.setFocused(true);
    await settle();
    expect(inbox.getCurrentResult().data?.pages).toEqual([page("first")]);

    onlineManager.setOnline(false);
    delivered = page("second", "first");
    await advance(60_000);
    expect(inbox.getCurrentResult().data?.pages).toEqual([page("first")]);
    onlineManager.setOnline(true);
    await settle();
    expect(inbox.getCurrentResult().data?.pages).toEqual([delivered]);
  });

  it("refreshes only active lists and preserves search inputs and loaded pages", async () => {
    const inactive = listKey("sent");
    client.setQueryData(inactive, { pages: [page("sent")], pageParams: [0] });
    const detailsKey = [["mail", "get"], { input: { id: "old" }, type: "query" }];
    client.setQueryData(detailsKey, { id: "old", body: "open message" });
    const key = listKey("inbox", "from:friend@example.com");
    const latest = [{ ...page("new", "old"), nextPageToken: 1 }, page("older")];
    const inbox = observe(
      async (cursor) => latest[cursor]!,
      [{ ...page("old", "older"), nextPageToken: 1 }, page("oldest")],
      key,
    );
    start();
    await advance();

    expect(inbox.getCurrentResult().data).toEqual({ pages: latest, pageParams: [0, 1] });
    expect(client.getQueryData<InfiniteData<Page>>(inactive)).toEqual({
      pages: [page("sent")],
      pageParams: [0],
    });
    expect(client.getQueryData<{ id: string; body: string }>(detailsKey)).toEqual({
      id: "old",
      body: "open message",
    });
    expect(client.getQueryCache().findAll({ queryKey: [["mail", "listThreads"]] })).toHaveLength(2);
  });

  it("waits for optimistic actions, queued moves, and mutations before refreshing", async () => {
    const inbox = observe(async () => page("new"));
    start();
    store.set(optimisticActionsAtom, { read: { type: "READ", threadIds: ["old"], read: true } });
    await advance();
    expect(inbox.getCurrentResult().data?.pages).toEqual([page()]);

    store.set(backgroundQueueAtom, { type: "add", threadId: "thread:old" });
    store.set(optimisticActionsAtom, {});
    await advance();
    expect(inbox.getCurrentResult().data?.pages).toEqual([page()]);

    let finish!: () => void;
    const mutation = client.getMutationCache().build(client, {
      mutationFn: () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    });
    const action = mutation.execute(undefined);
    await settle();
    store.set(backgroundQueueAtom, { type: "clear" });
    await advance();
    expect(inbox.getCurrentResult().data?.pages).toEqual([page()]);
    finish();
    await action;
    await advance();
    expect(inbox.getCurrentResult().data?.pages).toEqual([page("new")]);
  });

  it("discards a slow poll after navigation without reverting the read-state patch", async () => {
    let finish!: (value: Page) => void;
    const inbox = observe(
      () =>
        new Promise<Page>((resolve) => {
          finish = resolve;
        }),
      [page("old")],
    );
    start();
    await advance();
    expect(inbox.getCurrentResult().isFetching).toBe(true);
    inbox.destroy();
    observe(async () => page("sent"), [page("sent")], listKey("sent"));
    const readPage = { ...page("old"), threads: [{ id: "old", hasUnread: false }] };
    client.setQueryData<InfiniteData<Page>>(listKey(), { pages: [readPage], pageParams: [0] });
    store.set(optimisticActionsAtom, { read: { type: "READ", threadIds: ["old"], read: true } });
    await settle();
    store.set(optimisticActionsAtom, {});
    finish(page("old"));
    await settle();

    expect(client.getQueryData<InfiniteData<Page>>(listKey())?.pages).toEqual([readPage]);
    expect(client.getQueryState(listKey())?.fetchStatus).toBe("idle");
    expect(client.getQueryState(listKey())?.status).toBe("success");
  });

  it("does not interrupt pagination and recovers from a failed background fetch", async () => {
    let finish!: (value: Page) => void;
    let fail = false;
    const first = { ...page("first"), nextPageToken: 1 };
    const inbox = observe(
      async (cursor) => {
        if (fail) throw new Error("temporary IMAP failure");
        if (cursor === 0) return first;
        return new Promise<Page>((resolve) => {
          finish = resolve;
        });
      },
      [first],
    );
    start();
    const next = inbox.fetchNextPage();
    await advance();
    expect(inbox.getCurrentResult().isFetchingNextPage).toBe(true);
    finish(page("second"));
    await next;
    expect(inbox.getCurrentResult().data?.pages).toEqual([first, page("second")]);

    fail = true;
    await advance();
    expect(inbox.getCurrentResult().isError).toBe(true);
    expect(inbox.getCurrentResult().data?.pages).toEqual([first, page("second")]);
    fail = false;
    await advance();
    finish(page("second", "third"));
    await settle();
    expect(inbox.getCurrentResult().data?.pages).toEqual([first, page("second", "third")]);
    expect(inbox.getCurrentResult().isError).toBe(false);
  });

  it("stops timers and event refreshes when the layout unmounts or the account changes", async () => {
    const inbox = observe(async () => page("new"));
    const stop = start();
    stop();
    await advance(60_000);
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    onlineManager.setOnline(false);
    onlineManager.setOnline(true);
    await settle();
    expect(inbox.getCurrentResult().data?.pages).toEqual([page()]);
  });
});
