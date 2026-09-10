/* Equaliser bands, presets and AutoEq import. */

(() => {
  "use strict";

  const BANDS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];

  const PRESETS = {
    "Neutral": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Bass-Boost": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "Bass-Absenkung": [-6, -5, -4, -2, 0, 0, 0, 0, 0, 0],
    "Höhen-Boost": [0, 0, 0, 0, 0, 1, 3, 4, 5, 6],
    "Höhen-Absenkung": [0, 0, 0, 0, 0, -1, -3, -4, -5, -6],
    "Stimme": [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
    "Rock": [5, 4, 2, -1, -2, 0, 2, 4, 5, 5],
    "Pop": [-1, 0, 2, 4, 4, 2, 0, -1, -1, -1],
    "Jazz": [4, 3, 1, 2, -1, -1, 0, 1, 2, 4],
    "Klassik": [4, 3, 2, 0, -1, -1, 0, 2, 3, 4],
    "Electronic": [5, 4, 1, 0, -2, 2, 1, 1, 4, 5],
    "Hip-Hop": [6, 5, 2, 3, -1, -1, 1, 0, 2, 3],
    "Podcast": [-6, -4, 0, 3, 5, 5, 4, 2, 0, -2],
    "Loudness": [6, 4, 0, -2, -3, -2, 0, 3, 6, 7],
    "Over-Ear-Ausgleich": [4, 3, 1, 0, -1, -1, 0, 2, 3, 2],
    "In-Ear-Ausgleich": [2, 1, 0, -1, -2, -1, 1, 3, 4, 3],
  };

  const FILTER_TYPES = { PK: "peaking", LS: "lowshelf", HS: "highshelf", LSC: "lowshelf", HSC: "highshelf" };

  /* AutoEq publishes two text formats. "ParametricEQ" carries real filter
     definitions, which we can map onto biquads one-to-one; "GraphicEQ" is a
     dense frequency/gain table that we resample onto our ten bands. */

  function parseParametric(text) {
    const filters = [];
    let preamp = 0;

    for (const line of text.split(/\r?\n/)) {
      const preampMatch = line.match(/^\s*Preamp:\s*(-?[\d.]+)\s*dB/i);
      if (preampMatch) {
        preamp = parseFloat(preampMatch[1]);
        continue;
      }
      const filterMatch = line.match(
        /^\s*Filter\s+\d+:\s*ON\s+(\w+)\s+Fc\s+([\d.]+)\s*Hz\s+Gain\s+(-?[\d.]+)\s*dB\s+Q\s+([\d.]+)/i
      );
      if (filterMatch) {
        filters.push({
          type: FILTER_TYPES[filterMatch[1].toUpperCase()] || "peaking",
          frequency: parseFloat(filterMatch[2]),
          gain: parseFloat(filterMatch[3]),
          q: parseFloat(filterMatch[4]),
        });
      }
    }

    return filters.length ? { mode: "parametric", preamp, filters } : null;
  }

  function parseGraphic(text) {
    const match = text.match(/GraphicEQ:\s*(.+)/i);
    if (!match) return null;

    const points = [];
    for (const pair of match[1].split(";")) {
      const [frequency, gain] = pair.trim().split(/\s+/).map(Number);
      if (Number.isFinite(frequency) && Number.isFinite(gain)) points.push([frequency, gain]);
    }
    if (points.length < 2) return null;

    points.sort((a, b) => a[0] - b[0]);

    const gains = BANDS.map((band) => {
      if (band <= points[0][0]) return points[0][1];
      if (band >= points[points.length - 1][0]) return points[points.length - 1][1];
      for (let i = 1; i < points.length; i += 1) {
        const [highFrequency, highGain] = points[i];
        if (highFrequency >= band) {
          const [lowFrequency, lowGain] = points[i - 1];
          const ratio = (band - lowFrequency) / (highFrequency - lowFrequency);
          return lowGain + (highGain - lowGain) * ratio;
        }
      }
      return 0;
    }).map((gain) => Math.round(gain * 10) / 10);

    // AutoEq's graphic curves are boost-only, so pull the whole thing down to
    // leave headroom instead of clipping.
    const preamp = -Math.max(0, ...gains);
    return { mode: "graphic", preamp: Math.round(preamp * 10) / 10, gains };
  }

  function parseAutoEq(text) {
    if (!text || !text.trim()) return null;
    return parseParametric(text) || parseGraphic(text);
  }

  window.SM = window.SM || {};
  window.SM.EQ = { BANDS, PRESETS, parseAutoEq };
})();
