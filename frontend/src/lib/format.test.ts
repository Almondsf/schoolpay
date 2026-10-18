import { describe, expect, it } from 'vitest';

import { formatKobo, koboToInput, parseNairaToKobo } from './format';

describe('money', () => {
  it.each([
    ['150000', 15_000_000],
    ['150,000.5', 15_000_050],
    ['₦ 1,234.56', 123_456],
    ['0.1', 10],
    ['0.07', 7],
  ])('parses %s', (input, kobo) => expect(parseNairaToKobo(input)).toBe(kobo));

  it.each(['', 'abc', '1.234', '-5', '1e5', '12.'])('rejects %s', (input) => expect(parseNairaToKobo(input)).toBeNull());

  it('allows negatives when asked (adjustments)', () => {
    expect(parseNairaToKobo('-2,000', { allowNegative: true })).toBe(-200_000);
  });

  it('round-trips through the input format', () => {
    for (const kobo of [0, 7, 10, 15_000_050, -200_000]) expect(parseNairaToKobo(koboToInput(kobo), { allowNegative: true })).toBe(kobo);
  });

  it('formats kobo as naira', () => {
    expect(formatKobo(15_000_050).replace(/\s/g, '')).toBe('₦150,000.50');
  });
});
