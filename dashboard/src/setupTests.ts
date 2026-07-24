import "@testing-library/jest-dom";

// Polyfill ResizeObserver for recharts testing in jsdom
global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};
