import "@testing-library/jest-dom";

// Polyfill ResizeObserver for recharts testing in jsdom
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};
