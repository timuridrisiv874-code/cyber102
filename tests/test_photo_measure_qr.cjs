const test = require('node:test');
const assert = require('node:assert/strict');
const {qrSquare, hasCrispQrEdge, lengthMm} = require('../web/photo-measure.js');

function box(points) {
  return Object.fromEntries(
    ['topLeftCorner','topRightCorner','bottomRightCorner','bottomLeftCorner'].map(
      (key, i) => [key,{x:points[i][0],y:points[i][1]}]));
}

test('square geometry uses original pixels and the black edge, independently of image orientation', () => {
  const points = qrSquare(box([[150,70],[230,130],[170,210],[90,150]]),1,400,250);
  assert.deepEqual(points[0],{x:150/400,y:70/250});
  const estimate = lengthMm({reference:points.slice(0,2),referenceMm:40,target:[{x:0.1,y:0.2},{x:0.15,y:0.2}]},400,250);
  assert.ok(Math.abs(estimate-8)<1e-8);
});

test('small, cropped, oblique and distorted squares cannot provide a scale', () => {
  assert.throws(() => qrSquare(box([[10,10],[45,10],[45,45],[10,45]]),1,200,200),/мелкий/);
  assert.throws(() => qrSquare(box([[0,10],[80,10],[80,90],[0,90]]),1,200,200),/полями/);
  assert.throws(() => qrSquare(box([[15,10],[145,10],[125,100],[35,100]]),1,180,140),/углом/);
  assert.throws(() => qrSquare(box([[15,10],[115,10],[150,104],[50,104]]),1,180,140),/углом/);
});

test('a broad blur or low contrast across the printed edges is rejected', () => {
  const width=145, height=145, left=20, right=125, modules=21;
  const corners=qrSquare(box([[left,left],[right,left],[right,right],[left,right]]),1,width,height);
  function pixels(blur, contrast) {
    const data=new Uint8ClampedArray(width*height*4);
    for (let y=0;y<height;y++) for (let x=0;x<width;x++) {
      const signed=Math.min(x-left,right-x,y-left,right-y);
      const shade=255-contrast*Math.max(0,Math.min(1,0.5+signed/blur));
      const i=4*(y*width+x);
      data[i]=data[i+1]=data[i+2]=shade;
      data[i+3]=255;
    }
    return {data,width,height};
  }
  assert.ok(hasCrispQrEdge(pixels(1,220),corners,modules));
  assert.equal(hasCrispQrEdge(pixels(9,220),corners,modules),false);
  assert.equal(hasCrispQrEdge(pixels(1,30),corners,modules),false);
});
