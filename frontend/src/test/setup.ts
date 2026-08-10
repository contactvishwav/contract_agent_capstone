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

// jsdom does not implement the object URL APIs at all (blob URL creation
// for local image previews and authenticated attachment fetches - input.tsx,
// AttachmentImage.tsx). Real browser behavior isn't under test here, just
// that these components call create/revoke correctly - a deterministic
// fake string keeps assertions simple without needing a real Blob backend.
if (typeof globalThis.URL.createObjectURL === 'undefined') {
  let counter = 0;
  globalThis.URL.createObjectURL = () => `blob:test-${++counter}`;
  globalThis.URL.revokeObjectURL = () => undefined;
}
