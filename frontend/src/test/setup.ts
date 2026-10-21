import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

// The suite runs files in parallel; on a busy machine or CI runner a first render can take longer
// than Testing Library's default 1 s, so findBy/waitFor get more time (they still return as soon as ready)
configure({ asyncUtilTimeout: 5000 });

afterEach(() => cleanup());
