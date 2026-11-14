const test = require('node:test');
const assert = require('node:assert/strict');
const {pumpThreshold} = require('../web/inspection.js');
const catalog = require('../data/customer/equipment-passports.json');
const serial = '21031004-1';
const digits = text => text.replace(/[\u00a0\u202f]/g, ' ');
const firstPump = catalog.equipment.find(item => item.kind === 'pump' && item.serial === serial);

test('the paired source page and difference are read from the selected pump passport', () => {
  const result = pumpThreshold(catalog, serial, 'manual', 8000);
  assert.equal(digits(result.terms), 'Срок службы — 20 лет. Ресурс до списания — не менее 11 000 ч.');
  assert.match(digits(result.hours), /3 000 ч.*8 000 ч/);
  assert.deepEqual([...result.sources], [['/api/customer/documents/pump-1-passport/pdf#page=3', 'Паспорт · стр. 3']]);
  assert.equal(pumpThreshold(catalog, '21031004-2', '', NaN).sources.keys().next().value,
    '/api/customer/documents/pump-2-passport/pdf#page=3');
});

test('values and citations can change independently; the UI does not embed a model-specific minimum', () => {
  const other = structuredClone(firstPump);
  other.serial = 'another-pump';
  other.fields.design_resource_before_writeoff.value = 9200;
  other.fields.design_lifetime.value = 15;
  other.fields.design_lifetime.source.page = 4;
  const changed = pumpThreshold({equipment:[other]}, other.serial, 'manual', 9100.5);
  assert.match(digits(changed.terms), /15 лет.*9 200 ч/);
  assert.match(changed.hours, /99,5 ч/);
  assert.equal(changed.sources.size, 2);
});

test('blank, demo, wrong serial and accessory readings cannot supply aggregate running hours', () => {
  for (const [source, reading] of [['manual', NaN], ['manual', null], ['manual', -1], ['', 8000], ['demo', 8000]]) {
    assert.match(pumpThreshold(catalog, serial, source, reading).hours, /укажите суммарную наработку/);
  }
  for (const unbound of ['', 'unknown', 'P-102', catalog.equipment.find(item => item.kind === 'motor').serial]) {
    assert.equal(pumpThreshold(catalog, unbound, 'manual', 8000), null);
  }
  assert.equal(pumpThreshold(null, serial, 'manual', 8000), null);
});

test('zero is a real reading; reaching or passing the minimum never predicts a failure or negative reserve', () => {
  assert.match(digits(pumpThreshold(catalog, serial, 'manual', 0).hours), /11 000 ч.*0 ч/);
  for (const reading of [11000, 12000]) {
    assert.match(pumpThreshold(catalog, serial, 'manual', reading).hours, /минимум достигнут/);
    assert.doesNotMatch(pumpThreshold(catalog, serial, 'manual', reading).hours, /До паспортного минимума|отказ/);
  }
});

test('unsupported unit or missing/unsafe citation does not produce a calculation or an external link', () => {
  const other = structuredClone(firstPump);
  other.fields.design_resource_before_writeoff.unit = 'дни';
  let result = pumpThreshold({equipment:[other]}, serial, 'manual', 500);
  assert.equal(result.hours, '');
  assert.equal(result.terms, 'Срок службы — 20 лет.');
  other.fields.design_lifetime.source.documentId = '../outside';
  result = pumpThreshold({equipment:[other]}, serial, 'manual', 500);
  assert.equal(result, null);
});
