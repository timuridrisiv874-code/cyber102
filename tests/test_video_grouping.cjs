const test = require('node:test');
const assert = require('node:assert/strict');
const { groupObservations, summarize, evidenceTime } = require('../web/video-review.js');

function frame(index, component = 'pipe', condition = 'corrosion', status = 'defect') {
  return { frameIndex: index, timestampSeconds: index * 2 + 1, component, condition, status,
    box: [20, 30, 800, 900], observation: `Наблюдение ${index}` };
}

test('overview names just the confirmed refinement classes once, alongside distinct primary findings', () => {
  const grouped = groupObservations([frame(0)]);
  assert.equal(summarize(grouped, ['terminal_damage', 'crack', 'crack', 'unrecognized']),
    'Модель отметила коррозию на 1 из 1 выбранных кадров. При уточнении отмечены признаки трещины и повреждения клеммы или проводки.');
  const clear = groupObservations([frame(0, 'pipe', 'no_visible_defect', 'clear')]);
  assert.equal(summarize(clear, ['terminal_damage']), 'При уточнении отмечены признаки повреждения клеммы или проводки.');
  assert.equal(summarize(clear, []), 'На выбранных кадрах явных внешних дефектов не замечено.');
  assert.equal(summarize(clear, ['unrecognized']), summarize(clear));
});

test('repeated classes share one group with chronological evidence, without mutating input', () => {
  const rows = [frame(3), frame(1), frame(0), frame(2)];
  const original = structuredClone(rows);
  const grouped = groupObservations(rows);
  assert.equal(grouped.mode, 'defects');
  assert.equal(grouped.groups.length, 1);
  assert.deepEqual(grouped.groups[0].observations.map(row => row.frameIndex), [0, 1, 2, 3]);
  assert.deepEqual(rows, original);
  assert.equal(grouped.groups[0].observations[0].observation, 'Наблюдение 0');
});

test('different components and different defects remain separate across the whole clip', () => {
  const rows = [frame(0), frame(1, 'pipe', 'wet_patch'), frame(2, 'motor'),
    frame(3, 'pump', 'visible_damage'), frame(4, 'valve'), frame(5)];
  const grouped = groupObservations(rows);
  assert.equal(grouped.groups.length, 5);
  assert.deepEqual(grouped.groups.map(group => [group.component, group.condition]),
    [['pipe', 'corrosion'], ['pipe', 'wet_patch'], ['motor', 'corrosion'], ['pump', 'visible_damage'], ['valve', 'corrosion']]);
  assert.equal(grouped.groups[0].observations.length, 2);
});

test('eight clear frames do not create eight cards', () => {
  const rows = Array.from({ length: 8 }, (_, index) => frame(index, 'pump', 'no_visible_defect', 'clear'));
  const grouped = groupObservations(rows);
  assert.equal(grouped.mode, 'clear');
  assert.equal(grouped.clearCount, 8);
  assert.equal(grouped.uncertainCount, 0);
  assert.deepEqual(grouped.groups, []);
});

test('mixed clear and uncertain evidence never becomes an all-clear conclusion', () => {
  const rows = [frame(0, 'pump', 'no_visible_defect', 'clear'), frame(1, 'none', 'uncertain', 'unusable')];
  const grouped = groupObservations(rows);
  assert.equal(grouped.mode, 'partial');
  assert.equal(grouped.clearCount, 1);
  assert.equal(grouped.uncertainCount, 1);
  assert.equal(grouped.groups.length, 0);
});

test('uncertain and inconsistent labels do not produce a clear result', () => {
  for (const rows of [[frame(0, 'none', 'uncertain', 'unusable')],
    [frame(0, 'none', 'no_visible_defect', 'clear')], [frame(0, 'pump', 'corrosion', 'clear')],
    [frame(0, 'unknown_component', 'no_visible_defect', 'clear')]]) {
    assert.equal(groupObservations(rows).mode, 'uncertain');
    assert.equal(groupObservations(rows).groups.length, 0);
  }
});

test('one finding can recur after clean, uncertain or other findings; all actual moments remain reachable', () => {
  const rows = [frame(0), frame(1), frame(2, 'pump', 'no_visible_defect', 'clear'), frame(3),
    frame(4, 'none', 'uncertain', 'unusable'), frame(5), frame(6, 'valve', 'wet_patch'), frame(7)];
  const result = groupObservations(rows);
  assert.deepEqual(result.groups.map(group => group.episodes.map(episode => episode.map(row => row.frameIndex))),
    [[[0, 1], [3], [5], [7]], [[6]]]);
  assert.equal(summarize(result), 'Модель отметила коррозию и следы жидкости на 6 из 8 выбранных кадров. Кадров без оценки: 1.');
  assert.deepEqual(result.clearFrames, [rows[2]]);
  assert.deepEqual(result.uncertainFrames, [rows[4]]);
});

test('empty response, every frame unusable and partially assessable footage have different summaries', () => {
  const clear = frame(0, 'pump', 'no_visible_defect', 'clear');
  const unclear = frame(1, 'none', 'uncertain', 'unusable');
  assert.equal(groupObservations([]).mode, 'empty');
  const summaries = [[], [unclear], [clear], [clear, unclear]].map(rows => summarize(groupObservations(rows)));
  assert.equal(new Set(summaries).size, 4);
  assert.match(summaries[3], /1 из 2/);
});

test('short clip timecodes keep distinct fractional moments and minute rounding', () => {
  assert.deepEqual([0.03125, 0.09375, 0.25, 59.9997, 65.5].map(evidenceTime),
    ['0:00.031', '0:00.094', '0:00.25', '1:00', '1:05.5']);
});

test('uncertain frames remain counted alongside grouped defects', () => {
  const grouped = groupObservations([frame(0), frame(1, 'pump', 'uncertain', 'unusable'),
    frame(2, 'motor', 'no_visible_defect', 'clear')]);
  assert.equal(grouped.mode, 'defects');
  assert.equal(grouped.uncertainCount, 1);
  assert.equal(grouped.clearCount, 1);
  assert.equal(grouped.frameCount, 3);
});

test('duplicate frame identities and invalid timestamps are rejected', () => {
  assert.throws(() => groupObservations([frame(0), frame(0)]));
  assert.throws(() => groupObservations([{ ...frame(0), timestampSeconds: Infinity }]));
  assert.throws(() => groupObservations([{ ...frame(0), timestampSeconds: -1 }]));
  assert.throws(() => groupObservations([{ ...frame(0), frameIndex: 0.5 }]));
  assert.throws(() => groupObservations(null));
});
