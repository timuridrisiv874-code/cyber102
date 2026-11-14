const test = require('node:test');
const assert = require('node:assert/strict');
const {sourcePoint, rectangle, fits} = require('../web/region-review.js');

test('portrait and wide previews map CSS coordinates into original pixels', () => {
  assert.deepEqual(sourcePoint(95, 140, {left:20,top:40,width:100,height:400}, 1000,4000), {x:750,y:1000});
  assert.deepEqual(sourcePoint(150, 42.5, {left:20,top:10,width:520,height:130}, 8000,2000), {x:2000,y:500});
});

test('reverse selection encloses fractional edge pixels and clamps a drag beyond the preview', () => {
  const display = {left:20,top:40,width:100,height:400};
  const end = sourcePoint(-90,900,display,1000,4000);
  const box = rectangle({x:823.1,y:12.9},end);
  assert.deepEqual(box,{x:0,y:12,width:824,height:3988});
  assert.equal(fits(box,1000,4000),true);
  assert.deepEqual(rectangle(sourcePoint(20,40,display,1000,4000),sourcePoint(120,440,display,1000,4000)),
    {x:0,y:0,width:1000,height:4000});
});

test('zero-area gestures and invalid numeric input cannot produce a confirmed crop', () => {
  assert.equal(sourcePoint(2,2,{left:0,top:0,width:0,height:3},10,10),null);
  assert.equal(rectangle({x:5.6,y:3.1},{x:5.6,y:9.3}),null);
  for (const bad of [{x:-1,y:0,width:4,height:5},{x:1,y:2,width:0,height:3},
    {x:0,y:0,width:1.5,height:2},{x:1,y:2,width:10,height:1},{x:1,y:NaN,width:2,height:2}]) {
    assert.equal(fits(bad,10,10),false);
  }
});
