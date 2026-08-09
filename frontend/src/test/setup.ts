import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(cleanup);

Object.defineProperty(Element.prototype, 'scrollIntoView', {
  configurable: true,
  value: () => undefined,
});

// pdfjs-dist's browser canvas module references DOMMatrix at import time.
// jsdom does not implement it; component render math is covered in-browser.
if (typeof globalThis.DOMMatrix === 'undefined') {
  globalThis.DOMMatrix = class TestDOMMatrix {} as typeof DOMMatrix;
}
