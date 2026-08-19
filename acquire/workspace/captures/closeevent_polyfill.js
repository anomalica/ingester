// Polyfill the `CloseEvent` global for single-file-cli's simple-cdp dependency.
// single-file-cli spawns Chromium over a WebSocket via simple-cdp, which
// constructs a `CloseEvent` when the socket closes. `CloseEvent` is only a
// Node global from v23; the container's Node is older, so single-file crashed
// with "ReferenceError: CloseEvent is not defined" and produced no snapshot.
// Loaded via NODE_OPTIONS=--require before single-file runs.
if (typeof globalThis.CloseEvent === "undefined") {
  globalThis.CloseEvent = class CloseEvent extends Event {
    constructor(type, init = {}) {
      super(type, init);
      this.code = init.code ?? 0;
      this.reason = init.reason ?? "";
      this.wasClean = init.wasClean ?? false;
    }
  };
}
