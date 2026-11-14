const test = require('node:test');
const assert = require('node:assert/strict');
const {pumpThreshold, motorThreshold} = require('../web/inspection.js');
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

test('each exact motor serial resolves its own assigned hours, years and source page', () => {
  for (const [motor, source] of Object.entries({'230201068':'drive-1-passports/pdf#page=2',
    '230201069':'drive-2-passports/pdf#page=2', '230201067':'drive-3-passports/pdf#page=4',
    '230201070':'drive-4-passports/pdf#page=2'})) {
    const result = motorThreshold(catalog, motor, 12000);
    assert.equal(digits(result.terms), 'Назначенный срок службы — 20 лет. Назначенный ресурс — 60 000 ч.');
    assert.equal(digits(result.remaining), 'До назначенного ресурса — 48 000 ч.');
    assert.deepEqual([...result.sources.keys()], ['/api/customer/documents/' + source]);
  }
  assert.equal(motorThreshold(catalog, serial, 12000), null);
  assert.equal(motorThreshold(catalog, 'missing', 12000), null);
});

test('motor hours require their own reading; blank, negative and zero values have separate meanings', () => {
  for (const reading of [undefined, null, '', NaN, -1, Infinity]) {
    assert.equal(motorThreshold(catalog, '230201068', reading).remaining, '');
  }
  assert.match(digits(motorThreshold(catalog, '230201068', 0).remaining), /60 000 ч/);
  for (const reading of [60000, 61000]) {
    assert.equal(motorThreshold(catalog, '230201068', reading).remaining, 'Назначенный ресурс по наработке достигнут.');
  }
});

test('unsupported motor resource unit or citation suppresses only the affected field', () => {
  const motor = structuredClone(catalog.equipment.find(item => item.serial === '230201068'));
  motor.fields.assigned_resource.source.documentId = '../wrong';
  const limited = {equipment: [motor]};
  const result = motorThreshold(limited, motor.serial, 500);
  assert.equal(result.acceptsHours, false);
  assert.equal(result.remaining, '');
  assert.equal(result.terms, 'Назначенный срок службы — 20 лет.');
  motor.fields.assigned_lifetime.unit = 'месяцы';
  assert.equal(motorThreshold(limited, motor.serial, 500), null);
});
